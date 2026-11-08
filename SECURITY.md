# Security and personal data

Run a separate MyGG installation for each person. Everyone you authorize in one installation has access to its connected accounts, to-dos, memory and other stored data. A private Telegram conversation alone does not isolate those records between users.

## Access and approvals

- Set `ALLOWED_USER_IDS` to your own positive numeric Telegram ID before starting. GG processes authorized users in private chats. To receive briefings, `OWNER_CHAT_ID` must be one of those IDs.
- Sending email, replying to email, sending WhatsApp text and inviting calendar guests require approval in the requesting Telegram chat. Check the recipient and content. The newest preview expires after ten minutes, including across a restart. When delivery status is uncertain, check the destination before sending again.
- The optional WhatsApp bridge uses a secret from `.env` and a loopback HTTP receiver. Keep the receiver, Ollama and Qdrant accessible only on your own computer. Telegram polling requires no publicly exposed server port.

## Where data goes

Telegram carries messages and uploads. In Claude mode, your prompts, relevant conversation history, personality instructions, attachments, recalled memories and tool results can go to Anthropic. This includes data retrieved from Gmail, WHOOP or WhatsApp when those integrations are active. Edge TTS sends the text of a spoken reply to its speech service. Local mode runs model inference in Ollama. Configured services still receive requests needed to carry out their functions.

Optional web search sends queries to the search provider. The optional Jina reader receives the URL it renders. Cloud vector knowledge lookup sends the question to OpenAI and queries the configured Qdrant index. Chatterbox runs speech synthesis on the host and can use the owner's private reference recording. Local files used for board and knowledge lookup can contribute text to the selected chat model's prompt. Claude Code handles owner-requested development in a temporary checkout using the owner's account.

Keep these files and stores private, including backups and screenshots:

| Item | What it contains or grants |
| --- | --- |
| `.env` | Bot token, API keys, optional integration secrets and IDs. |
| `credentials.json`, `token*.json`, `.whoop_token.json` | OAuth client configuration and account access grants. |
| `pa.db` and companion files, `meeting_transcripts/`, Qdrant data | Recent chats, outgoing drafts and receipts, goals, to-dos, reminders, meetings, WhatsApp and memories. |
| `whatsapp-bridge/auth_state/`, `whatsapp-bridge/qr.png` | Linked-device session or a pairing code. |
| `whatsapp-bridge/pending-delivery/` | Message contents waiting to be saved by the local receiver. |
| `personality.md`, `.current_location`, logs | Personal details, location and diagnostic information that can identify people or accounts. |
| `private/`, reference audio, project board, knowledge folders | Owner-supplied voice and source documents. Keep personal content out of shared source. |

If you share MyGG, send the repository link. A GitHub source download contains the code and example files. A copy of an installed folder can contain credentials and personal data. Revoke any exposed key with its provider and unlink an exposed WhatsApp session from your phone.

## Report a vulnerability

Contact the repository owner privately if you already have a direct channel. Otherwise open an issue asking for a private reporting channel and omit the exploit details and personal data. Include reproduction steps and the affected version once a private channel is established. [MyGG issue tracker](https://github.com/pedropedropedropedrop/MyGG/issues)
