"use client";

import { Check, X } from "lucide-react";
import { useState, useTransition } from "react";

type Props = {
  domain: string;
  initialVerdict: string | null;
  initialNotes: string | null;
};

export function ReviewControls({ domain, initialVerdict, initialNotes }: Props) {
  const [notes, setNotes] = useState(initialNotes ?? "");
  const [verdict, setVerdict] = useState(initialVerdict ?? "");
  const [isPending, startTransition] = useTransition();

  function submit(nextVerdict: "approved" | "rejected") {
    startTransition(async () => {
      const response = await fetch(`/api/leads/${encodeURIComponent(domain)}/review`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ verdict: nextVerdict, notes })
      });

      if (response.ok) {
        setVerdict(nextVerdict);
      }
    });
  }

  return (
    <div className="review">
      <textarea
        aria-label={`Review notes for ${domain}`}
        value={notes}
        onChange={(event) => setNotes(event.target.value)}
        rows={2}
      />
      <div className="reviewActions">
        <button
          className="iconButton approve"
          disabled={isPending}
          onClick={() => submit("approved")}
          title="Approve lead"
          type="button"
        >
          <Check size={16} />
        </button>
        <button
          className="iconButton reject"
          disabled={isPending}
          onClick={() => submit("rejected")}
          title="Reject lead"
          type="button"
        >
          <X size={16} />
        </button>
        <span className={`verdict ${verdict || "none"}`}>{verdict || "open"}</span>
      </div>
    </div>
  );
}
