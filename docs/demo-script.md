# 15-minute demo and defence script

## Before recording

1. Start the backend on port `8000`.
2. Start the frontend on port `3000`.
3. Open two browser contexts:
   - Browser A: employee
   - Browser B: IT support
4. Use a hard refresh before recording.
5. Keep the process-flow diagram open in a tab or slide.
6. Do not show `.env`, API keys, MongoDB credentials, or terminal secrets.

### Demo credentials

| Role | Email | Password |
| --- | --- | --- |
| Employee | `employee@veridian.local` | `Employee-Demo-01!` |
| IT support | `agent@veridian.local` | `Agent-Demo-01!` |
| Admin | `admin@veridian.local` | `Admin-Demo-01!` |

If the demo accounts have been changed, use the current values from the local
development environment rather than displaying them in the recording.

## Minute-by-minute script

### 0:00–0:45 — Opening

**Say:**

> This is Veridian IT Desk, an internal IT support agent. It does not simply
> generate a chatbot answer. It creates a ticket, searches the approved policy
> base, validates the proposed action, and either resolves, asks a bounded
> follow-up, or hands the case to IT.

Show the light employee login page briefly.

### 0:45–1:45 — Architecture and roles

Open [`process-flow.md`](process-flow.md) or the exported diagram.

**Say:**

> There are three roles. Employees use the ticket workspace. IT support uses a
> shared inbox and joins conversations. Admins manage users and policy. The
> model is MiMo-V2.5 through OpenCode Go, but the decision boundaries are Python
> tools backed by the knowledge base.

Point to the `Guardrail checks` box in the diagram.

### 1:45–3:15 — Employee login and automatic ticket creation

In Browser A, log in as the employee and open `/help`.

Click **New ticket** and select:

> How do I reset my password?

**Say while it runs:**

> The first message creates a ticket and a concise ticket title before the first
> response. The user sees the response stream in the transcript, not a raw
> provider trace.

Point out:

- ticket number;
- generated title;
- cited/approved answer;
- typing indicator;
- no duplicate message;
- one Send/Stop action slot.

Expected policy: **KB-01 Password Reset**. This is the “AI can safely answer”
path.

### 3:15–4:30 — Quick-answer path

Start another ticket and choose:

> My printer says paper jam even when there isn't one.

**Say:**

> This is another policy-supported request. The agent can provide the approved
> first steps from KB-05 and explain when a ticket needs the printer asset tag.

Show the cited policy and the employee-facing response.

### 4:30–6:15 — Refusal and routing path

Start another ticket and choose:

> I need access to the expense management tool.

**Say:**

> This looks like an IT request, but the source says access belongs to Finance.
> The agent must not pretend to grant it. The ownership guardrail routes it to
> the correct function instead of inventing an approval.

Point out that the internal audit keeps the source and decision reason while the
employee receives a concise message.

### 6:15–7:45 — Material policy conflict

Start another ticket and use:

> My laptop is not turning on and it is about 3.5 years old. Can I get a replacement?

**Say:**

> This is deliberately ambiguous. KB-03 describes a three-year replacement rule
> while ASSET-01 describes a four-year cycle with Finance involvement. Between
> three and four years, the system cannot safely choose a policy, so it cites
> both and hands the ticket to IT.

Show the concise employee handoff chip and the cited policy/audit state.

### 7:45–9:00 — IT inbox

In Browser B, log in as `agent@veridian.local` and open `/inbox`.

**Say:**

> IT sees one operational queue, not separate inbox and assigned pages. Tickets
> are in the left rail, while the selected conversation shows the transcript.

Open the laptop ticket.

Point out:

- employee messages are incoming;
- AI responses are outgoing in the IT perspective;
- the ticket list lives in the left rail;
- cited policy and audit trail remain in the right context panel.

### 9:00–10:30 — Human handoff

Click **Join — take ownership**.

**Say:**

> Joining changes the conversation owner. The employee receives a live join
> notice, and later employee messages are delivered to the human instead of
> starting a second AI response.

In Browser A, show the employee-side state update.

Send from Browser B:

> Hi, I have picked this up. I am reviewing the replacement policy and will help with the next step.

Show it in both views.

### 10:30–11:30 — Human-only continuation

In Browser A, send:

> I have already tried charging it and it still has no lights.

**Say:**

> This is now a human-owned thread. The message is persisted and delivered to
> IT, but the AI does not produce an extra greeting or answer over the human.

In Browser B, reply:

> Thanks, I have enough information to continue this with the hardware team.

### 11:30–12:15 — Resolve and reopen behavior

Click **Resolve** in Browser B.

**Say:**

> Human resolution and AI resolution are tracked separately. If the employee
> sends a new message after a human resolution, it returns to the IT handoff
> path. If an AI-resolved ticket is reopened, the AI can handle the new message
> first.

This demonstrates that “resolved” is not an irreversible dead end.

### 12:15–13:15 — Admin and knowledge base

Log in as admin in a third browser context or log out of Browser B.

Open `/manage/kb`.

**Say:**

> The knowledge base is configurable by an admin. Editing a clause changes the
> source the agent can retrieve, while conflicts and overrides remain visible.

Optionally show `/manage/team` and the role controls. Avoid making a permanent
edit during the recording unless you restore it afterwards.

### 13:15–14:15 — Authentication and RBAC defence

Demonstrate one quick check:

1. Log in as employee.
2. Navigate directly to `/inbox`.
3. Show redirect/denial.
4. Log out.
5. Open `/help` and show return to login.

**Say:**

> Authentication is backed by the FastAPI API, with role checks enforced on the
> server. The frontend hides unavailable navigation, but the backend is the
> actual authorization boundary.

### 14:15–15:00 — Close and invite questions

**Say:**

> The important design choice is that uncertainty is visible rather than hidden.
> The system can resolve simple policy-backed work, ask for missing facts, or
> hand off ambiguous and owned decisions. Each path leaves a persisted ticket,
> source trail, and role-appropriate view.

Then stop screen sharing and move to questions.

## Backup demo prompts

Use these if the model is slow or a previous ticket is already resolved:

| Prompt | Demonstrates |
| --- | --- |
| `How do I reset my password?` | KB-01, safe resolution |
| `My printer says paper jam even when there isn't one.` | KB-05, troubleshooting answer |
| `I need access to the expense management tool.` | Finance ownership / routing |
| `I need approval to install software that's not in the catalog.` | IT Security review |
| `My laptop is not turning on and it is 3.5 years old.` | Material policy conflict / handoff |
| `My laptop is not turning on and it is 4.5 years old.` | Both laptop thresholds support eligibility |

## Defence questions and answers

### Why not trust the model's answer?

The model only proposes a tool call. `resolve`, `raise_ticket`, and `escalate`
are Python functions with explicit checks. A model cannot bypass a refusal by
writing a confident paragraph.

### Why does the laptop case escalate?

The three-to-four-year case has conflicting supplied policies and no precedence
rule. Escalation is safer than silently choosing Finance or IT. At 4.5 years,
the material conflict is no longer ambiguous and both clauses support the
replacement threshold.

### What happens if retrieval is wrong?

The current retrieval is intentionally simple keyword scoring because the demo
knowledge base has eleven clauses. A larger deployment should use embeddings or
a hybrid search index plus evaluation sets.

### Is the ticket a real ServiceNow/Jira ticket?

No. It is a structured, persisted application ticket for the prototype. An
external ITSM connector would be the next integration boundary.

### What is the role of SSE?

SSE lets the employee and IT views receive streamed AI events and live join,
reply, and resolve messages without polling or refreshing the page.

### How do you prevent AI from talking over IT?

The backend checks the conversation state before running the model. Escalated
or human-assigned threads use a quiet handoff stream; they do not call
`agent.run()`.

## Video and Drive checklist

- Record the entire 15-minute run, including the architecture diagram.
- Use a clean browser profile or hide unrelated tabs/bookmarks.
- Do not show `.env`, API keys, MongoDB URIs, or terminal output containing
  credentials.
- Keep the employee and IT browser windows visibly separate.
- Upload the final video to Google Drive.
- Set sharing to **Anyone with the link — Viewer**.
- Test the link in an incognito window before submission.
- Name the file clearly, for example:
  `Veridian-IT-Desk-Assignment-2-Demo.mp4`.
