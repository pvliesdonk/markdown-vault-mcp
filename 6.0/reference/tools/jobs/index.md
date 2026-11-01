# Jobs

## `get_job_result`

**Get Job Result.** Read-only. Tags: `jobs`.

Get the status of a background job started by a long-running tool on this server; returns status (working, completed or failed) with the result or error once it finishes. Job records expire, so fetch a finished job's result promptly. On this server, background jobs come from slow summarize, reindex, and `build_embeddings` calls.

**Parameters**

| Name     | Type     | Default  | Description                                                                    |
| -------- | -------- | -------- | ------------------------------------------------------------------------------ |
| `job_id` | `string` | required | The `job_id` a long-running tool returned when it continued in the background. |

**Returns**

`object`

```
{"job_id": "j_3f9a"}
```

While the job runs: `{"job_id": "j_3f9a", "status": "working", "result": null, "error": null, "running_for_s": 41.3, "retry_after_s": 5.0, "message": "Still running. …"}`. When it is done: `{"job_id": "j_3f9a", "status": "completed", "result": {…}, "error": null}`, with the tool's own result; `"failed"` carries `error` instead. An unknown, expired or another caller's `job_id` is refused the same way. Records expire `MARKDOWN_VAULT_MCP_JOBS_RESULT_TTL_S` after creation (one hour by default).
