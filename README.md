# Invoices

Just simple script to generate simple invoices and list from asana.

## Directories
* inputs -- input jsons for each invoice
* invoices -- where all invoice data is stored. Usually it's symlink to something else (i.e. dropbox directory)
* parties -- definitions of parties (i.e you or your client)

## How to use

1. Create invoices directory (i.e. symlink to dropbox folder, or whatever.
2. Create 2 parties in parties directory, one for you, one for your client. See parties/sample.json for
  format. The name of the file describes the party identifier, which is then used in other json (i.e. if you
  have parties/me.json, you will refer to it as "me" in other jsons).
3. Create secrets.json file with your name, and supply asana token (or not, and it will skip doing asana). See
  secrets-sample.json for format.
4. Create input json for your invoice. See inputs/sample.json as example  
5. `python make_invoice.py --invoice-file inputs/input_for_current_month.json` generates pdf report (in
  invoices directory)
6. `python make_invoice.py --increment-from inputs/previous_month.json --invoice-file
  inputs/input_for_current_month.json -- first take template from previous month, update there dates and so, and
  generate input for next month. Then it will open an editor, where you can change stuff (like amount of work)
  and then it will generate pdf.
