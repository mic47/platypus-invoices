import argparse
from dateutil.parser import parse as parse_date
import json
import sys

import asana
from jinja2 import Template
import weasyprint


def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--invoice-file", type=str)
    parser.add_argument("--parties", type=str, default="parties")
    parser.add_argument("--template", type=str, default="template.html")
    parser.add_argument("--asana-template", type=str, default="template-asana.html")
    parser.add_argument("--secrets", type=str, default="secrets.json")
    parser.add_argument(
        "--output-prefix", type=str, default="invoices/{supplier}_{client}_{payment_reference}"
    )
    return parser.parse_args()


def get_project_mapping(asana_client, workspace):
    return {
        project["gid"]: project["name"]
        for project in asana_client.projects.find_all({"workspace": workspace})
    }


def sanitize_long_words(sentence: str, word_len_limit: int):
    words = sentence.split(" ")
    out = []
    for word in words:
        ww = []
        while len(word) > word_len_limit:
            ww.append(word[:word_len_limit])
            word = word[word_len_limit:]
        ww.append(word)
        out.append("&#8203;".join(ww))
    return " ".join(out)


def get_completed_tasks(asana_client, workspace, date_from, date_to):
    date_from = parse_date(date_from, dayfirst=True).date()
    date_to = parse_date(date_to, dayfirst=True).date()
    projects = get_project_mapping(asana_client, workspace)
    for task in asana_client.tasks.find_all(
        {
            "workspace": workspace,
            "assignee": "me",
            "completed_since": date_from.isoformat(),
            "opt_fields": ["name", "resource_type", "completed", "completed_at", "projects"],
        }
    ):
        if not task["completed"]:
            continue
        completed_at = parse_date(task["completed_at"]).date()
        if completed_at < date_from or completed_at > date_to:
            continue
        task["completed_at_day"] = completed_at.strftime("%d.%m.%Y")
        for project in task["projects"]:
            project["name"] = projects[project["gid"]]
        task["projects_string"] = ", ".join(p["name"] for p in task["projects"])
        task["name"] = sanitize_long_words(task["name"], 20)
        yield task


def load_secrets(secret_file, who):
    with open(secret_file) as f:
        return json.load(f)[who]


def generate_invoice(args, data, output_prefix):
    # Dump json data
    with open(f"{output_prefix}.json", "w") as f:
        json.dump(data, f)
    # Render template
    with open(args.template) as f:
        t = Template("".join(f))
    rendered_html = t.render(**data)
    # Generate HTML and PDF
    with open(f"{output_prefix}.html", "w") as f:
        print(rendered_html, file=f)
    weasyprint.HTML(string=rendered_html).write_pdf(f"{output_prefix}.pdf")


def generate_attachment_asana(args, data, output_prefix, secrets):
    asana_client = asana.Client.access_token(secrets["asana_token"])
    tasks = list(
        sorted(
            get_completed_tasks(asana_client, secrets["asana_workspace"], data["date_from"], data["date_to"]),
            key=lambda x: x["completed_at_day"],
        )
    )
    with open(f"{output_prefix}_asana.json", "w") as f:
        json.dump(tasks, f)
    with open(args.asana_template) as f:
        t = Template("".join(f))
    rendered_html = t.render(tasks=tasks, **data)
    with open(f"{output_prefix}_asana.html", "w") as f:
        print(rendered_html, file=f)
    weasyprint.HTML(string=rendered_html).write_pdf(f"{output_prefix}_asana.pdf")


def expand_data(args, data):
    # Compute totals
    total = 0
    for delivery in data["deliveries"]:
        delivery["total"] = delivery["quantity"] * delivery["unit_price"]
        total += delivery["total"]
    data["total"] = total

    # Add supplier and client values
    for prefix in ["supplier", "client"]:
        value = data[prefix]
        with open(f"{args.parties}/{value}.json") as f:
            for k, v in json.load(f).items():
                data[f"{prefix}_{k}"] = v
    return data


def main():
    args = parse_arguments()
    with open(args.invoice_file) as f:
        data = json.load(f)
    secrets = load_secrets(args.secrets, data["supplier"])
    output_prefix = args.output_prefix.format(**data)

    data = expand_data(args, data)
    generate_invoice(args, data, output_prefix)
    generate_attachment_asana(args, data, output_prefix, secrets)


if __name__ == "__main__":
    main()
