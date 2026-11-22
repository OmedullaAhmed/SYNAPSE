# Data protection position — DRAFT for review

> Draft prepared from how the software works. It is not legal advice and must be reviewed (and the statutory references
> checked against the current text of the Digital Personal Data Protection Act, 2023 and its Rules) before it is shared
> with a customer.

## Roles

- **Customer (retailer / distributor):** Data Fiduciary for the personal data in its systems.
- **StockFlow provider:** Data Processor, acting only on the customer's documented instructions (contract / DPA).
- **Sub-processors (only if the customer switches the feature on):** hosting provider (customer's choice of region),
  Anthropic (AI answers; reading handwritten count sheets), Meta / Twilio (WhatsApp alerts), the customer's own ERP/WMS.

## Personal data StockFlow processes

| Data | Whose | Why | Kept |
|---|---|---|---|
| User name / work email, role, locations, password hash, last sign-in | Customer staff | Access control | While the account exists |
| Audit trail: user, action, time, what changed | Customer staff | Accountability for stock decisions | Customer's choice (default: kept) |
| WhatsApp numbers of stores, warehouses and managers | Customer staff (business contacts) | Transfer alerts | While in the customer's sheets |
| Photos / scans of count sheets | May show staff names or signatures | Reading stock counts | Not stored by default (read, then discarded); the counted figures are kept |
| Sales lines in stored input files | No personal data kept (customer columns are dropped on reading) | Rebuilding a plan after a restart | 90 days, then purged |

**Not processed:** end-customer names, phone numbers, emails or loyalty ids in POS exports are never read into StockFlow.

## Points to confirm

1. Notice to staff users about the audit trail and, if enabled, AI reading of count-sheet images.
2. Retention period for the audit trail (contractual and statutory record-keeping needs).
3. Cross-border transfer: hosting in an Indian region keeps data in India; enabling Anthropic features sends prompts or
   images to a provider outside India. Confirm this against the current position on transfers under the Act and the
   customer's own policies (and sector regulators where relevant).
4. Breach notification: processor-to-fiduciary notice period in the contract, aligned with the fiduciary's obligations under the Rules.
5. Data principal requests (access, correction, erasure) for staff: handled by the customer's admin (edit or deactivate the
   user); audit entries keep the user name for accountability: confirm the basis for keeping them.
6. Deletion at contract end: export (CSV / database backup) then deletion of the database and backups, with confirmation.
