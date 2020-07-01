import argparse
import copy
import os
import re
from dateutil.parser import parse as parse_date
import json
import sys
from datetime import datetime, timedelta

import asana
from jinja2 import Template
import weasyprint


def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--invoice-file", type=str)
    parser.add_argument("--increment-from", type=str)
    parser.add_argument("--parties", type=str, default="parties")
    parser.add_argument("--template", type=str, default="template.html")
    parser.add_argument("--asana-template", type=str, default="template-asana.html")
    parser.add_argument("--oncall-template", type=str, default="template-oncall.html")
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
        return json.load(f).get(who, {})


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

def generate_oncall(args, data, output_prefix):
    with open(args.oncall_template) as f:
        t = Template("".join(f))
    rendered_html = t.render(**data)
    # Generate HTML and PDF
    with open(f"{output_prefix}_oncall.html", "w") as f:
        print(rendered_html, file=f)
    weasyprint.HTML(string=rendered_html).write_pdf(f"{output_prefix}_oncall.pdf")

def generate_attachment_asana(args, data, output_prefix, secrets):
    if "asana_token" not in secrets:
        print("Asana token is not present, skipping generating asana", file=sys.stderr)
        return
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
    def recompute_if_missing(key, replacement):
        if key not in data:
            data[key] = replacement

    # Compute dates
    recompute_if_missing("delivery_date", data["date_to"])
    recompute_if_missing(
        "due_date", (parse_date(data["issue_date"], dayfirst=True) + timedelta(days=15)).strftime("%d.%m.%Y")
    )

    # Compute oncall
    if "oncall" in data:
        for sheet in data["oncall"]:
            title = sheet["title"]
            b_start = time_to_hours(sheet["business_start"])
            b_end = time_to_hours(sheet["business_end"])
            hourly_price = sheet["hourly_price"]
            total_hours = 0
            for item in sheet["items"]:
                if item["workday"]:
                    hours = hours_outside_business(b_start, b_end, time_to_hours(item["from"]), time_to_hours(item["to"]))
                else:
                    f, t = time_to_hours(item["from"]), time_to_hours(item["to"])
                    assert f < t
                    hours = t - f

                item["hours"] = hours
                item["price"] = hours * hourly_price
                total_hours += hours
            sheet["total_hours"] = total_hours
            sheet["total_price"] = total_hours * hourly_price
            data["deliveries"].append({
                "description": title,
                "quantity": total_hours,
                "unit_price": hourly_price,
                "unit": "hour",
            })

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

def time_to_hours(s):
    h, minutes = [int(x) for x in s.split(':')]
    return h+minutes/60


def hours_outside_business(business_start, business_end, time_start, time_end):
    assert time_start < time_end
    assert business_start < business_end
    if time_end <= business_start or time_start >= business_end:
        # no overlap
        return time_end - time_start
    return max(0, business_start - time_start) + max(0, time_end - business_end)



def end_of_month(date):
    date = copy.copy(date)
    date.replace(day=28)
    day = timedelta(days=1)
    cur_month = date.month
    while (date + day).month == cur_month:
        date = date + day
    return date


def parse_pretty_date(s):
    return parse_date(s, dayfirst=True)


def pretty_date(date):
    return date.strftime("%d.%m.%Y")


PR_FORMATS = [
    "^(?P<prefix>..)(?P<year>{year})(?P<number>[0-9]*)$",
    "^(?P<prefix>)(?P<year>{year})(?P<number>[0-9*])$",
    "^(?P<year>)(?P<prefix>.*[^0-9])(?P<number>[0-9]*)$",
    "^(?P<year>)(?P<prefix>)(?P<number>[0-9]*)$",
]


def increment_payment_reference(pr, prev_year, this_year):
    match = None
    for f in PR_FORMATS:
        m = re.match(f, pr)
        if m is not None:
            match = m
            break
    if not match:
        raise Exception(f"failed to parse payment reference {pr}")
    prefix = match.group("prefix")
    year = match.group("year")
    number = match.group("number")
    if year == str(prev_year):
        year = str(this_year)
    num_len = len(number)
    number = str(int(number) + 1)
    number = "0" * (num_len - len(number)) + number
    return f"{prefix}{year}{number}"


def copy_and_increment(input_file, output_file):
    if os.path.exists(output_file):
        print(f"Output file {output_file} exists!", file=sys.stderr)
        sys.exit(1)
    with open(input_file) as f:
        data = json.load(f)
    if "delivery_date" in data:
        del data["delivery_date"]
    if "due_date" in data:
        del data["due_date"]

    today = datetime.now().date()

    date_to_previous = parse_date(data["date_to"], dayfirst=True).date()
    date_from = date_to_previous + timedelta(days=1)
    date_to = end_of_month(date_from)

    if today - date_to > timedelta(5):
        print(
            f"WARNING: Generating invoice more than 5 days old ({date_to}, {today})! Bailing out",
            file=sys.stderr,
        )
    data["date_from"] = pretty_date(date_from)
    data["date_to"] = pretty_date(date_to)
    data["issue_date"] = pretty_date(today)

    data["payment_reference"] = increment_payment_reference(
        data["payment_reference"], date_to_previous.year, date_to.year
    )

    with open(output_file, "w") as f:
        json.dump(data, f, indent=4)


def main():
    args = parse_arguments()
    if args.increment_from is not None:
        copy_and_increment(args.increment_from, args.invoice_file)
        os.system(f"$EDITOR {args.invoice_file}")
    with open(args.invoice_file) as f:
        data = json.load(f)
    secrets = load_secrets(args.secrets, data["supplier"])
    output_prefix = args.output_prefix.format(**data)

    data = expand_data(args, data)
    generate_invoice(args, data, output_prefix)
    generate_attachment_asana(args, data, output_prefix, secrets)
    if "oncall" in data:
        generate_oncall(args, data, output_prefix)


if __name__ == "__main__":
    main()
