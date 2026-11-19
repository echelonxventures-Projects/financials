# Using it from other languages

The engine is Python, but nothing about using it is. Every input and output is plain
data (JSON/YAML configs in; JSON, CSV or Excel out), and there are three ways in, none of
which needs a Python binding:

| Route | Best for | Needs |
|---|---|---|
| **CLI as a JSON filter** | Scripts, batch jobs, desktop apps, any language that can start a process | The `financial-model` command on the machine |
| **REST API** | Services, web front ends, many callers, other machines | A running `financial-model serve` (or the container image) |
| **JSON Schema** | Building or checking configs in an editor or in your own code | `schemas/config.schema.json` (a static file) |

Working, tested examples for **JavaScript (Node), Go, Ruby, C# and POSIX shell** are in
[`examples/`](https://github.com/echelonxventures-Projects/financials/tree/main/examples).
Each one runs through both routes, and CI checks that every language reports the same
valuation.

## 1. CLI as a JSON filter

```bash
financial-model run - -o -   < config.json  > result.json
```

- `-` as the config reads it from **stdin**: JSON or YAML.
- `-o -` (or `-f json` with no `-o`) writes the **full result as JSON on stdout**, and
  nothing else: no banners or progress lines.
- On error the exit code is non-zero, stdout is empty and **stderr** ends with one line
  starting `Error:`. A bad option exits with 2, a bad config with 1.
- `ma - -o -` does the same for an M&A deal, and `validate -` checks a config from stdin.

The JSON has the same shape as the `.json` export: `income_statement`, `balance_sheet` and
`cash_flow` as `{period: {line: value}}`; `bs_balanced` / `cf_balanced`; `periods`;
`metadata`; and `analysis` holding the model's extra tables (`DCF Valuation`,
`Free Cash Flow`, `Sensitivity`, LBO `Returns`, ...).

```js
// JavaScript
import { execFileSync } from "node:child_process";
const out = execFileSync("financial-model", ["run", "-", "-o", "-"], { input: JSON.stringify(config) });
const ev = JSON.parse(out).analysis["DCF Valuation"].ev_gordon;
```

```go
// Go
cmd := exec.Command("financial-model", "run", "-", "-o", "-")
cmd.Stdin = bytes.NewReader(configJSON)
stdout, err := cmd.Output() // err is *exec.ExitError on a bad config; details on stderr
```

The same pattern works from Java (`ProcessBuilder`), Rust (`std::process::Command`), R
(`system2(..., input=)`), PHP (`proc_open`), Excel VBA/Power Query, and anything else
that can run a program.

## 2. REST API

```bash
financial-model serve                       # or the OCI image; see Deployment
curl -s localhost:8000/dcf -H 'Content-Type: application/json' -d '{"config": {...}}'
```

The API publishes an **OpenAPI 3.1** spec at `GET /openapi.json` (interactive docs at
`/docs`). It can also be printed without starting a server: `financial-model openapi -o openapi.json`.
Feed it to any OpenAPI generator to get a typed client in your language, for example:

```bash
npx @openapitools/openapi-generator-cli generate -i openapi.json -g typescript-fetch -o client/
# other generators: go, java, csharp, rust, kotlin, swift5, php, ruby, r, dart, ...
```

Endpoints, auth (`X-API-Key`) and error codes are listed in [CLI & API](interfaces.md).

## 3. JSON Schema for configs

`schemas/config.schema.json` (JSON Schema draft 2020-12, regenerate with
`financial-model schema -o schemas/config.schema.json`) describes every config the
toolkit accepts: single models and M&A deals, picked by `model_type`. Use it to:

- **Get autocomplete and checking in your editor.** Put this as the first line of a YAML
  config (VS Code with the YAML extension, JetBrains IDEs, Neovim with yaml-language-server):

  ```yaml
  # yaml-language-server: $schema=https://raw.githubusercontent.com/echelonxventures-Projects/financials/main/schemas/config.schema.json
  ```

- **Validate configs in your own code** with any JSON Schema library (ajv for JS,
  `santhosh-tekuri/jsonschema` for Go, `networknt` for Java, `JsonSchema.Net` for .NET, ...).
- **Generate types** for building configs (`quicktype`, `json-schema-to-typescript`, ...).

The schema is checked against the example configs in CI, and a test fails if the committed
file drifts from the code.
