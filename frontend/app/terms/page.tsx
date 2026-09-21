import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Terms of Service",
  description: "Terms of Service for the Veridian IT Desk demo deployment.",
};

/* Plain prose page: no backend, no auth needed. */

const SECTIONS: { heading: string; body: string }[] = [
  {
    heading: "What this is",
    body: "Veridian IT Desk is an assignment submission built for the AIONOS Agentic AI Factory assessment (Assignment 2, Internal Service Agent). It is a demonstration and evaluation artefact, not a full-fledged production application: there is no service-level agreement, no uptime commitment, and no support obligation of any kind.",
  },
  {
    heading: "Demo use only",
    body: "Accounts on this deployment are provisioned demo logins (employee, IT agent, admin) for trying the product. Do not enter real personal data, real credentials, or confidential company information into chats, tickets, or knowledge-base edits — demo content may be visible to other reviewers of this deployment.",
  },
  {
    heading: "Acceptable use",
    body: "Use the workspace for its intended purpose: raising and working IT support requests. Do not attempt to access accounts or threads that are not yours, disrupt the service, or use it for anything unlawful.",
  },
  {
    heading: "AI-generated content",
    body: "Replies from the desk agent are produced by a language model grounded in a small policy knowledge base. Answers cite their sources, but they can be wrong. Verify anything important with your real IT department before acting on it.",
  },
  {
    heading: "No warranty",
    body: "The service is provided as-is, without warranties of any kind. The authors accept no liability for decisions made on the basis of demo content.",
  },
];

export default function TermsPage() {
  return (
    <main className="mx-auto w-full max-w-2xl flex-1 px-4 py-12">
      <h1 className="text-2xl font-bold tracking-tight text-slate-900">Terms of Service</h1>
      <p className="mt-2 text-sm text-slate-500">
        Veridian IT Desk · demo deployment for assessment purposes.
      </p>
      <div className="mt-8 flex flex-col gap-6">
        {SECTIONS.map((s) => (
          <section key={s.heading}>
            <h2 className="text-base font-semibold text-slate-900">{s.heading}</h2>
            <p className="mt-1 text-sm leading-relaxed text-slate-600">{s.body}</p>
          </section>
        ))}
      </div>
    </main>
  );
}
