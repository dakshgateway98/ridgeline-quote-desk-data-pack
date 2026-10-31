# Session interaction log

At the start of each session, read `.chat-history/log.md` for context. After each assistant response, append one entry in the exact format below; create the directory/file when needed and never delete prior entries. Do this silently without asking. Be precise about files affected in that response. Replace secrets, tokens, passwords, and `.env` values with `***` in every field.

---
- timestamp: "<ISO 8601 timestamp if available, otherwise estimate based on conversation order>"
- user_prompt: "<the user's original prompt>"
- assistant_response_summary: "<concise, specific summary>"
- files_affected: "<comma-separated files created or modified, or none>"
