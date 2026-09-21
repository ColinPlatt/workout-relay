# File-delivery probe

Before building activity retrieval, settle one question: **can ChatGPT and
Claude actually read the contents of a FIT or TCX file delivered over MCP, from
a phone?** Everything else in that feature — OAuth, connected-assistant
management, Garmin downloads — is wasted work if the files do not arrive.

This probe is the experiment, not the feature. It serves two fixed sample
activities and never touches Garmin, the database or any account.

## What it exposes

Enabled with `MCP_PROBE_ENABLED=true`, it mounts a remote MCP server at
`/mcp/` offering both delivery shapes, because clients differ in what they
surface:

| Kind | Name | Notes |
|---|---|---|
| Tool | `list_recent_activities` | Metadata only, shaped like the future real endpoint |
| Tool | `get_activity_file` | Returns the file; `delivery` selects the packaging |
| Tool | `describe_activity_file` | Facts parsed from the file, to check the assistant's claims |
| Resource | `activity://sample-*.fit` / `.tcx` | The same files, read directly |

### Delivery modes

Claude rejected an embedded resource typed `application/vnd.ant.fit` as an
unsupported type: the tool call succeeded but no bytes reached the model. The
`delivery` argument exists to find out why, by changing only the packaging:

| `delivery` | Packaging |
|---|---|
| `resource` | Embedded resource, the format's own MIME type (the rejected case) |
| `octet` | Embedded resource, typed `application/octet-stream` |
| `text` | A plain text block containing base64 |
| `json` | A JSON object whose `data` field holds base64 |

`resource` failing while `octet` works points at MIME-type filtering; both
failing while `text` works points at blob resources; all of them failing points
at binary content in any form. The answer decides how activity retrieval ships
files, and it matters for size: the same activity is 29 KB as FIT against about
760 KB as TCX, large enough that a client may spill it to disk.

Samples: a 20-minute run (20 track points) and a two-hour run (1800 points,
about 29 KB of FIT and 740 KB of TCX). FIT travels base64-encoded in a blob;
TCX travels as text. If a client handles only one of those, that is the result
the experiment is looking for.

## Running the test

1. Set `MCP_PROBE_ENABLED=true` on the deployment and wait for it to restart.
   Leave everything else alone; the probe needs no account and reaches no
   user data.
2. Add `https://<your-host>/mcp/` as a custom connector in the assistant, on
   the phone, and note whether connecting is possible without a desktop.
   ([Claude connectors](https://claude.com/docs/connectors/building) ·
   [ChatGPT MCP connections](https://developers.openai.com/plugins/deploy/connect-chatgpt))
3. In an ordinary conversation, ask: **"List my sample activities, then fetch
   the FIT file for the long run and tell me the first GPS position, the
   starting heart rate and how many track points it contains."**
4. Repeat for the TCX file, and for the short activity.
5. Compare the answer with `describe_activity_file`, which reports the byte
   count, digest prefix and expected track-point count from the server side.

Turn the probe off again when finished.

### Reading the result

- **Pass** — the assistant reports values that match the file: track points
  1800, first position near 48.857°N 2.353°E, starting heart rate 140.
- **Fail** — it can only offer a download link, says it cannot read binary
  data, silently summarises the metadata instead of the file, or truncates the
  long activity. Record which of these happened, for which format, on which
  platform. A failure here is a genuine result: report it rather than working
  around it with summaries, manual uploads or a dedicated GPT.

The samples are synthetic, so a rejected FIT could in principle be the file's
fault. `fitdecode`, an independent parser, reads them correctly in the test
suite. To remove the doubt entirely, export a real activity from Garmin
Connect, save it as `sample-short-run.fit` (or `.tcx`) in a directory, and
point `MCP_PROBE_SAMPLES_DIR` at it; the probe serves the real file under the
same name.

## Safety

The probe is off by default and must stay off when no test is running. While
it is on, the endpoint is unauthenticated and anyone with the URL can fetch the
samples — acceptable only because the samples are synthetic and no Garmin or
account data is reachable through it. `MCP_PROBE_TOKEN` adds a bearer token if
the connector can send one, though most connector interfaces cannot.

`MCP_PROBE_ALLOWED_HOSTS` exists because the MCP SDK rejects requests whose
`Host` header it does not recognise, answering `421 Misdirected Request`. The
probe already trusts the host in `BASE_URL`; set this if the public hostname
differs.

## After the experiment

The probe is not a foundation to build on. `list_recent_activities` returns the
metadata shape the real `GET /api/v1/activities` should return, so the contract
carries over, but the real feature needs per-user OAuth, Garmin downloads with
size and decompression limits, token-refresh coordination with uploads, and the
consent screen describing that these files contain GPS traces and health data.
Delete the probe once it has answered its question.
