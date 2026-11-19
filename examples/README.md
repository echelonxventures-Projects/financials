# Examples in other languages

Each example builds the same small DCF config natively, runs it and prints revenue by
year and the enterprise and equity values. None needs third-party packages.

| Language | Run (via the CLI) |
|---|---|
| JavaScript (Node 18+) | `node examples/node/valuation.mjs` |
| Go 1.21+ | `cd examples/go && go run .` |
| Ruby 2.6+ | `ruby examples/ruby/valuation.rb` |
| C# (.NET 10+) | `dotnet run examples/csharp/valuation.cs` |
| POSIX shell + curl | `sh examples/shell/valuation.sh` |

To use the REST API instead, start the server (`financial-model serve`) and set
`FIN_API_URL=http://127.0.0.1:8000`, plus `FIN_API_KEY` if the server has a key.
`FINANCIAL_MODEL` points the CLI examples at a different executable.

See [docs/other-languages.md](../docs/other-languages.md) for the contracts the examples rely on.
