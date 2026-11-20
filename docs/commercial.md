# Commercial platform: PaaS, B2B and B2C

The toolkit can be sold three ways from the same code base and the same deployment:

| Offering | Who buys | What they get | How it is delivered |
|---|---|---|---|
| **B2C** (self-serve) | Founders, students, analysts, freelancers | Personal account, Free or Pro plan, card checkout | `POST /v1/signup`, hosted checkout |
| **B2B** (teams) | Finance teams, advisors, accountants, funds | Organisation with members, roles, seats and shared usage; Team, Business or Enterprise plan | `POST /v1/organizations`, invoices or checkout |
| **PaaS** (API) | Software companies embedding models in their product | Tenant API keys, per-month run quotas, usage reports for invoicing | The REST API with `X-API-Key` |

Plus the **Community edition**: the MIT-licensed self-hosted toolkit, unchanged. With
`platform.enabled: false` (the default) the API behaves exactly as described in
[Self-hosting](self-hosting.md).

## Turning it on

```yaml
# financial.yaml
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

Every setting can also be set as an environment variable (`FIN_PLATFORM_ENABLED=1`,
`FIN_PLATFORM_BILLING_PROVIDER=stripe`, ...). Put a TLS-terminating proxy in front:
API keys and passwords must never cross the network in plain HTTP.

In platform mode:

* `/health`, `/docs`, `/openapi.json` and `/v1/plans`, `/v1/signup`, `/v1/login`,
  `/v1/billing/webhook` are public.
* Every other endpoint needs `X-API-Key: fm_...` (a tenant key) or
  `Authorization: Bearer <token>` (from `/v1/login`). The single shared `api.api_key`
  of self-hosted mode is not used.
* Model endpoints check the plan before running and count a run only when it succeeds.

## Plans

| Plan | Audience | Price (default) | Runs / month | Seats | Includes |
|---|---|---|---|---|---|
| Free | individual | $0 | 100 | 1 | three-statement, budget, PaaS, B2B, B2C models; JSON |
| Pro | individual | $19/mo, $190/yr | 5,000 | 1 | every model (DCF, LBO, M&A), Excel/CSV, charts, market data |
| Team | organization | $49 per seat/mo | 50,000 | 25 | Pro features, members and roles, 10 API keys |
| Business | organization | $999/mo | 500,000 | 100 | high-volume API (PaaS), unlimited periods, priority support |
| Enterprise | organization | contact sales | unlimited | unlimited | private deployment / on-prem licence, SLA, custom models |

These are defaults. Change prices, quotas and features, add tiers, or remove one in
`platform.plans` without touching code:

```yaml
platform:
  plans:
    pro: {price_monthly: 29, runs_per_month: 10000}
    startup: {name: Startup, audience: organization, price_monthly: 199, seats: 10, runs_per_month: 100000}
    business: null   # remove a tier
```

Plan fields: `name`, `audience` (`individual` | `organization` | `any`), `price_monthly`,
`price_yearly`, `per_seat`, `seats`, `runs_per_month`, `max_periods`, `model_types`
(`["*"]` = all), `exports` (`json`, `excel`, `csv`), `charts`, `data_fetch`, `api_keys`,
`support`, `description`, `features`. `null` for a limit means unlimited.
`financial-model platform plans` prints the effective catalogue; `GET /v1/plans`
returns it as JSON for a pricing page.

What a refusal looks like: `402` when the plan does not include the feature (upgrade),
`429` when the monthly run quota is used up, `403` when the caller's role is too low,
`401` without valid credentials.

## Accounts, roles and keys

* **Individual** accounts (B2C) have one owner. Sign-up creates the user, the account on
  `default_plan` and a first API key, shown once.
* **Organisations** (B2B) have members with a role: `owner` > `admin` > `member` > `viewer`.
  Admins add members (up to the plan's seats) and manage keys; viewers can read usage
  and listings but cannot run models. The last owner cannot be removed.
* A user can belong to several accounts. `/v1/login` takes an optional `tenant_id` to
  choose which one the token acts for; `/v1/me` lists them.
* API keys look like `fm_<prefix>_<secret>`. Only a SHA-256 digest is stored. Revoke with
  `DELETE /v1/keys/{id}`. Passwords are stored as PBKDF2-SHA256 hashes.

### API walkthrough

```bash
# B2C: sign up and run a model
curl -s localhost:8000/v1/signup -H 'Content-Type: application/json' \
  -d '{"email":"ada@example.com","password":"correct horse"}'
#  -> {"api_key": "fm_1a2b3c4d_...", "tenant": {"plan": "free", ...}}
python3 -c 'import sys,yaml,json;print(json.dumps({"config": yaml.safe_load(open(sys.argv[1]))}))' \
  config/b2c_subscription.yaml > request.json
curl -s localhost:8000/calculate -H "X-API-Key: $KEY" -H 'Content-Type: application/json' -d @request.json
curl -s localhost:8000/v1/usage -H "X-API-Key: $KEY"

# B2B: log in, create an organisation, add a colleague, issue a service key
TOKEN=$(curl -s localhost:8000/v1/login -d '{"email":"ada@example.com","password":"correct horse"}' \
  -H 'Content-Type: application/json' | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
curl -s localhost:8000/v1/organizations -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"name":"Acme Finance"}'
# log in again with {"tenant_id": "<org id>"} to act for the organisation, then:
curl -s localhost:8000/v1/members -H "Authorization: Bearer $ORG_TOKEN" \
  -H 'Content-Type: application/json' -d '{"email":"analyst@acme.com","role":"member"}'
curl -s localhost:8000/v1/keys -H "Authorization: Bearer $ORG_TOKEN" \
  -H 'Content-Type: application/json' -d '{"name":"erp-sync"}'
```

| Endpoint | Who | Purpose |
|---|---|---|
| `GET /v1/plans` | public | plan catalogue |
| `POST /v1/signup` | public | B2C account + first key |
| `POST /v1/login` | public | session token (optionally for a given `tenant_id`) |
| `GET /v1/me` | any | user, current account, plan, usage, other accounts |
| `POST /v1/organizations` | user | create a B2B organisation (caller becomes owner) |
| `GET/POST /v1/members`, `DELETE /v1/members/{id}` | admin | manage members and roles |
| `GET/POST /v1/keys`, `DELETE /v1/keys/{id}` | admin (owner for individuals) | API keys |
| `GET /v1/usage?period=YYYY-MM` | any | runs and other metrics this month |
| `POST /v1/billing/checkout` | admin | hosted checkout URL for a plan |
| `POST /v1/billing/webhook` | payment provider | signed subscription events |

## Billing

Billing is a plug-in, so the platform is not tied to one payment company.

* **`manual`** (default): no online payment. Sell by quote and invoice (usual for B2B and
  Enterprise) and apply the plan with `financial-model platform set-plan <tenant> <plan>`.
  `financial-model platform usage <tenant>` gives the month's usage as JSON for
  usage-based or overage invoices.
* **`stripe`**: `POST /v1/billing/checkout` returns a Stripe Checkout URL for the plan
  (quantity = seats for per-seat plans). Stripe calls `POST /v1/billing/webhook`; the
  signature is verified with your webhook secret, events are applied once each, and the
  account's plan and status follow the subscription (`active`, `past_due` blocks runs,
  `canceled` drops to free-tier limits while keeping the account and its keys). No Stripe
  SDK is needed.

  ```yaml
  platform:
    billing:
      provider: stripe
      stripe:
        secret_key: sk_live_...        # FIN_PLATFORM_BILLING_STRIPE_SECRET_KEY
        webhook_secret: whsec_...      # FIN_PLATFORM_BILLING_STRIPE_WEBHOOK_SECRET
        prices: {pro: price_..., team: price_..., business: price_...}
  ```

  Subscribe the webhook to `checkout.session.completed`, `customer.subscription.updated`,
  `customer.subscription.deleted` and `invoice.payment_failed`.

* **Other providers** (Paddle, Lemon Squeezy, Chargebee, your ERP): subclass
  `financial_model.platform.BillingProvider` (`checkout_url`, `parse_webhook`) and register
  it with `register_billing_provider("paddle", PaddleBilling)` or the
  `financial_model.billing_providers` entry point; select it with `billing.provider`.

## Operator CLI

```bash
financial-model platform plans                                   # effective catalogue
financial-model platform create-org "Acme Capital" --owner cfo@acme.com --plan business
financial-model platform add-member <tenant> analyst@acme.com --role admin
financial-model platform issue-key <tenant> --name erp-sync      # printed once
financial-model platform set-plan <tenant> enterprise            # after an invoice is paid
financial-model platform tenants                                 # accounts, plans, runs this month
financial-model platform usage <tenant> --period 2026-10         # JSON for invoicing
financial-model platform create-user solo@example.com --individual --password '...'
```

All commands take `--settings financial.yaml` and `--database sqlite:///...`.

## Storage and scaling

Accounts, keys and usage live in SQLite (standard library, one file, WAL mode). That is
enough for a single node with several workers. For several nodes, use a shared database:
implement `financial_model.platform.Store` and register a URL scheme with
`register_store("postgres", factory)` or the `financial_model.platform_stores` entry point,
then set `database: postgres://...`. Login tokens are signed, not stored, so any replica
with the same `secret_key` accepts them.

## Before charging customers

The code handles accounts, entitlements, metering and payments. Selling it also needs
decisions the code cannot make for you:

* **Licence.** The repository is MIT. Anyone may self-host it, including the platform
  code. If you want an open-core split (paid features under a commercial licence), move
  `financial_model/platform/` to a separately licensed package before you publish it.
* **Terms of service, privacy policy, data-processing agreement** (B2B buyers ask for a
  DPA), and sales tax / VAT handling. Stripe Tax or a merchant of record can cover tax.
* **Financial-advice disclaimer.** Model outputs are projections, not advice.
* **Operations:** TLS, backups of the platform database, monitoring of `/health`.
* **Not built in yet:** email delivery (verification, invitations, password reset), SSO /
  SAML for Enterprise, and a web front end. Users authenticate with API keys or
  `/v1/login`; a front end can be built on the `/v1` API.
