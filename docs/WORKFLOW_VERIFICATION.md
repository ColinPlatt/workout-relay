# Public workflow claims

Reviewed against repository `1605c4d` plus the working-tree changes on 2026-09-21.
This is code/mock-test verification, not a live Garmin/device certification.

| Claim | Evidence and limits |
| --- | --- |
| Plan in Claude/ChatGPT | `mcp_server.py`: authenticated format, validation, submission and status tools. The assistant generates the plan; Workout Relay does not run a model. Connector availability depends on the assistant account/workspace. |
| Schedule in advance | `garmin.py`: uploads/updates workouts and calls `schedule_workout` with the requested date. Submission/worker tests exercise this using mock data. Reusing workout IDs preserves identity; arbitrary new IDs can create new workouts. |
| Follow the workout on a device | Garmin Connect handles device synchronisation, not Workout Relay. Requires a compatible device and sync. Device delivery has not been tested here. |
| Read completed runs | `activities.py` and `mcp_server.py`: `list_activities` / `get_activity`, separately consented `activities:read`, allowlisted metrics and account-bound IDs. `test_activities.py` tests scope enforcement, metrics and isolation. The activity must have reached Garmin Connect first. |
| Analyse and adjust | The connected assistant can use the metrics for feedback and send a revised plan using existing workout IDs. Feedback quality is the assistant's responsibility; Workout Relay has no coaching model or automatic adaptation engine. |
| Automatic feedback immediately after a run | **Not implemented.** There is no completed-activity webhook, activity polling job, or outbound chat trigger. Retrieval occurs when a client invokes the tools. The user-facing wording says to ask the chat after syncing. |
| Access between visits | Persistent Garmin connections support later assistant calls; temporary connections expire and require another login. OAuth access and activity consent are still required. |

## Official references

- [ChatGPT custom MCP connection and account/workspace availability](https://developers.openai.com/plugins/deploy/connect-chatgpt).
- [Claude remote custom connectors and per-conversation tools](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp).
- [Garmin calendar scheduling and device sync](https://support.garmin.com/en-IN/?faq=XRcMvEtKdf7yBf8My9jua6).
- [Garmin device compatibility and completed activity upload on sync](https://www.garmin.com/en-US/blog/general/pre-made-workouts-from-garmin-connect/).

The public About page avoids hard-coded subscription-tier promises. The
OpenAI Docs check confirmed that availability can depend on account/workspace
policy. No real credentials, Garmin calls or deployment changes were used for
this review. A separately approved test-account/device exercise is required
to verify the entire production loop.
