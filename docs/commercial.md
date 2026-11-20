# Commercial platform: PaaS, B2B and B2C

The toolkit can be sold three ways from the same code base and the same deployment:

| Offering | Who buys | What they get | How it is delivered |
|---|---|---|---|
| **B2C** (self-serve) | Founders, students, analysts, freelancers | Personal account on a free, subscription or pay-as-you-go plan | `POST /v1/signup`, hosted checkout |
| **B2B** (teams) | Finance teams, advisors, accountants, funds | Organisation with members, roles, seats and shared usage | `POST /v1/accounts`, invoices or checkout |
| **PaaS** (API) | Software companies embedding models in their product | Account API keys, metered usage, pay-to-use pricing | The REST API with `X-API-Key` |

Plus the **Community edition**: the MIT-licensed self-hosted toolkit, unchanged. With
`platform.enabled: false` (the default) the API behaves exactly as described in
[Self-hosting](self-hosting.md).

## Zero hard-coding

No price, plan, quota, feature, role, account type, status, endpoint rule or billing
behaviour is written in Python. They are data, in two files shipped with the package:

| File | Holds | Override with |
|---|---|---|
| `financial_model/platform/catalog.yaml` | currency, account kinds, roles and permissions, account statuses, metrics, plans (prices, limits, features, pay-to-use metering), per-endpoint rules | inline keys under `platform:` in your settings file, or your own catalogue files via `platform.catalog` |
| `financial_model/platform/settings.yaml` | runtime defaults: database, token lifetime, password policy, API-key prefix, sign-up switch, billing provider settings (Stripe URL, signature tolerance, checkout fields, status map, webhook event handling) | the `platform:` section of your settings file, or `FIN_PLATFORM_*` environment variables |

Three ways to change the commercial model, from smallest to largest:

```yaml
# financial.yaml
platform:
  # 1. Change one value (deep-merged; everything else stays)
  plans:
    pro: {price: {monthly: 29}, metering: {runs: {included: 10000}}}
    business: null                       # remove a plan
    startup:                             # add a plan
      name: Startup
      audience: [organization]
      price: {monthly: 199, per_seat: false}
      limits: {seats: 10, api_keys: 5, periods: 240}
      model_types: ["*"]
      features: ["export:json", "export:excel", charts]
      metering: {runs: {included: 100000, overage: bill, unit_price: 0.004}}
  currency: EUR

  # 2. Layer your own catalogue file(s) over the built-in one
  catalog: [builtin, /etc/financial-model/pricing.yaml]

  # 3. Or replace the built-in catalogue entirely
  # catalog: [/etc/financial-model/catalog.yaml]
```

The catalogue is validated when the platform starts: an unknown plan, role, account
kind, status or metric referenced anywhere is an error, not a silent default.
`financial-model platform plans` prints the effective catalogue.

## Turning it on

```yaml
platform:
  enabled: true
  database: sqlite:////data/platform.db
  secret_key: "<long random string>"   # or FIN_PLATFORM_SECRET_KEY
```

```bash
financial-model serve --settings financial.yaml
# or, in containers:
FIN_PLATFORM_SECRET_KEY=... docker compose -f deploy/compose.platform.yaml up -d --build
```

Put a TLS-terminating proxy in front: API keys and passwords must never cross the
network in plain HTTP.

## Plans and pay-to-use

A plan has a fixed fee (optional), limits, the models and features it includes, and a
metering rule per metric:

```yaml
plans:
  payg:
    name: Pay as you go
    audience: [individual, organization]      # account kinds that may hold it
    price: {monthly: 0, yearly: null, per_seat: false}
    limits: {seats: 10, api_keys: 5, periods: 240}   # null = unlimited
    model_types: ["*"]
    features: ["export:json", "export:excel", "export:csv", charts, data_fetch]
    metering:
      runs:
        included: 0              # units in the fixed fee (null = unlimited)
        overage: bill            # block = refuse beyond included; bill = allow and charge
        tiers:                   # or a flat `unit_price: 0.02`
          - {up_to: 10000, unit_price: 0.02}
          - {up_to: null, unit_price: 0.01}
        cap: 1000000             # hard ceiling even when billing
    display: {support: email}    # free-form, returned by GET /v1/plans for a pricing page
```

That one shape covers every pricing model:

| Pricing model | How to express it |
|---|---|
| Free tier | `price.monthly: 0`, `overage: block` with an `included` allowance |
| Subscription | `price.monthly` / `price.yearly`, generous `included`, `overage: block` |
| Per seat | `price.per_seat: true` (fee x members) and `limits.seats` |
| Pay as you go | `price.monthly: 0`, `included: 0`, `overage: bill` with a price |
| Subscription + overage | `price.monthly` plus `included` and `overage: bill` |
| Volume / tiered | `tiers` |
| Unlimited / enterprise | `included: null`, `price.monthly: null` (contact sales) |

Metrics are defined in the catalogue (`metrics:`) and attached to endpoints
(`endpoints:`), so a new billable action is a catalogue entry. A metric a plan does not
list gets `default_metering` (built-in: not available).

The built-in plans (Free, Pay as you go, Pro, Team, Business, Enterprise) are
**placeholders**: set your own prices before charging anyone.

Refusals: `402` when the plan lacks a model, feature or limit (upgrade), `429` when an
allowance or cap is used up, `403` when the caller's role lacks the permission, `401`
without valid credentials.

`GET /v1/invoice` (and `financial-model platform invoice`) prices the month so far:
the fixed fee (x seats for per-seat plans) plus each metric's charge beyond its
allowance, tier by tier, in the catalogue's currency.

## Accounts, roles and endpoint rules

* **Account kinds** (`account_kinds`): built-in `individual` (B2C: one member, created by
  sign-up) and `organization` (B2B: unlimited members, created by users). Add or change
  kinds; `signup_kind` and `default_account_kind` say which kind each flow creates, and
  `default_plan` maps each kind to its starting plan.
* **Roles** (`roles`): a rank and a list of permissions (`"*"` = all). Built-in: owner,
  admin, member, viewer. A caller can only grant roles up to their own rank;
  `creator_role` is given to whoever creates an account and its last holder cannot be
  removed.
* **Statuses** (`account_statuses`): `blocked` refuses metered calls with a message,
  `use_lapsed_plan` applies `lapsed_plan` limits while keeping the account.
* **Endpoints** (`endpoints`): every route, keyed `"METHOD /path"`, says whether it is
  `public`, which `permission` it needs, which plan `features`, and which `metric` and
  `quantity` it meters. Usage is recorded only when the call succeeds. Routes not
  listed use `default_endpoint`.

```yaml
platform:
  roles:
    analyst: {rank: 15, permissions: [view, run]}
  endpoints:
    "POST /export/excel": {permission: export_files, metric: runs, quantity: 2, features: ["export:excel"]}
```

API keys look like `<api_key_prefix>_<id>_<secret>` (prefix `fm` by default); only a
SHA-256 digest is stored. Passwords are PBKDF2-SHA256 with the iteration count and
minimum length from the settings.

### API

| Endpoint | Default rule | Purpose |
|---|---|---|
| `GET /v1/plans` | public | catalogue for a pricing page (plans by account kind, metrics, currency) |
| `POST /v1/signup` | public | self-serve account (`signup_kind`) + first key |
| `POST /v1/login` | public | session token (optionally for a given `tenant_id`) |
| `GET /v1/me` | view | user, account, plan, usage, other accounts |
| `POST /v1/accounts` | view | create an account (`kind` default: `default_account_kind`) |
| `GET/POST /v1/members`, `DELETE /v1/members/{id}` | view / manage_members | members and roles |
| `GET/POST /v1/keys`, `DELETE /v1/keys/{id}` | view / manage_keys | API keys |
| `GET /v1/usage?period=YYYY-MM` | view | usage and remaining allowances |
| `GET /v1/invoice?period=YYYY-MM` | view | charges for the month |
| `POST /v1/billing/checkout` | manage_billing | hosted checkout URL for a plan |
| `POST /v1/billing/webhook` | public | signed payment-provider events |

```bash
curl -s localhost:8000/v1/signup -H 'Content-Type: application/json' \
  -d '{"email":"ada@example.com","password":"correct horse"}'     # -> {"api_key": "fm_..."}
python3 -c 'import sys,yaml,json;print(json.dumps({"config": yaml.safe_load(open(sys.argv[1]))}))' \
  config/b2c_subscription.yaml > request.json
curl -s localhost:8000/calculate -H "X-API-Key: $KEY" -H 'Content-Type: application/json' -d @request.json
curl -s localhost:8000/v1/invoice -H "X-API-Key: $KEY"
```

## Billing

The provider is chosen by `platform.billing.provider`; each reads the block named after it.

* **`manual`** (default): sell by quote and invoice. `financial-model platform invoice`
  gives each account's charges as JSON; apply plans with `platform set-plan`.
* **`stripe`**: Checkout for fixed fees (`prices: {plan: price_id}`), signed webhooks for
  subscription changes, and billing-meter events for pay-to-use (`meters: {metric:
  meter_event_name}`; run `financial-model platform sync-usage` on a schedule; it sends
  only units beyond each plan's allowance, once). The API base URL, timeout, signature
  tolerance, extra checkout fields, Stripe-status-to-account-status map and the handling
  of each webhook event type are settings:

  ```yaml
  platform:
    billing:
      provider: stripe
      stripe:
        secret_key: sk_live_...              # FIN_PLATFORM_BILLING_STRIPE_SECRET_KEY
        webhook_secret: whsec_...            # FIN_PLATFORM_BILLING_STRIPE_WEBHOOK_SECRET
        prices: {pro: price_..., team: price_...}
        meters: {runs: model_runs}
        checkout: {mode: subscription, allow_promotion_codes: "true"}
        events:
          charge.refunded: {status: past_due, tenant_from: metadata}
  ```

* **Other providers** (Paddle, Lemon Squeezy, Chargebee, an ERP): subclass
  `financial_model.platform.BillingProvider` (`checkout_url`, `parse_webhook`,
  `report_usage`) and register it with `register_billing_provider` or the
  `financial_model.billing_providers` entry point.

## Operator CLI

```bash
financial-model platform plans                       # effective catalogue, prices and pay-to-use rates
financial-model platform create-account "Acme Capital" --owner cfo@acme.com --plan business
financial-model platform add-member <account> analyst@acme.com --role admin
financial-model platform issue-key <account> --name erp-sync
financial-model platform set-plan <account> enterprise [--status active]
financial-model platform tenants                     # accounts, plans, usage this month
financial-model platform usage <account> --period 2026-10
financial-model platform invoice [<account>] --period 2026-10
financial-model platform sync-usage                  # push pay-to-use usage to the billing provider
financial-model platform create-user solo@example.com --signup --password '...'
```

All commands take `--settings financial.yaml` and `--database sqlite:///...`.

## Storage and scaling

Accounts, keys and usage live in SQLite (standard library, one file, WAL mode): enough
for one node with several workers. For several nodes, implement
`financial_model.platform.Store` and register a URL scheme with `register_store` or the
`financial_model.platform_stores` entry point. Login tokens are signed, not stored, so
any replica with the same `secret_key` accepts them.

## Before charging customers

* **Licence.** The repository is MIT, so anyone may self-host it, platform included. For
  an open-core split, move `financial_model/platform/` to a separately licensed package.
* **Terms of service, privacy policy, data-processing agreement**, and sales tax / VAT
  (Stripe Tax or a merchant of record can cover tax).
* **Financial-advice disclaimer.** Model outputs are projections, not advice.
* **Operations:** TLS, backups of the platform database, monitoring of `/health`.
* **Not built in yet:** email delivery (verification, invitations, password reset), SSO /
  SAML, and a web front end (one can be built on the `/v1` API).
