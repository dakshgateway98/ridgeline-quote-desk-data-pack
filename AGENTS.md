# Working in this project

## Conversation history

At the beginning of a session, read `.chat-history/log.md` so you can pick up where the previous work left off. After every response, add one entry for that exchange. Create the folder or log if either is missing, and keep all existing entries.

Use this format:

---
- timestamp: "<ISO 8601 timestamp, or a reasonable estimate>"
- user_prompt: "<the user's original prompt>"
- assistant_response_summary: "<a concise, specific summary of the response>"
- files_affected: "<comma-separated paths changed in this response, or none>"

List only files created or changed for the current response. Keep summaries short and concrete, naming important functions, endpoints, or decisions when relevant. Do this without interrupting the conversation to ask for permission.

Never put secrets in the log. Redact API keys, tokens, passwords, and values copied from `.env` files as `***` wherever they appear, including in the prompt text.
