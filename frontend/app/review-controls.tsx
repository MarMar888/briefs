"use client";

import { Check, Star, X } from "lucide-react";
import { useState, useTransition } from "react";

type Props = {
  domain: string;
  initialVerdict: string | null;
  initialNotes: string | null;
  initialStarred: boolean;
};

export function ReviewControls({ domain, initialVerdict, initialNotes, initialStarred }: Props) {
  const [notes, setNotes] = useState(initialNotes ?? "");
  const [savedNotes, setSavedNotes] = useState(initialNotes ?? "");
  const [verdict, setVerdict] = useState(initialVerdict ?? "");
  const [starred, setStarred] = useState(initialStarred);
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
        setSavedNotes(notes);
      }
    });
  }

  function saveNotes() {
    if (notes === savedNotes) return;
    startTransition(async () => {
      const response = await fetch(`/api/leads/${encodeURIComponent(domain)}/review`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ notes })
      });
      if (response.ok) setSavedNotes(notes);
    });
  }

  function toggleStar() {
    const next = !starred;
    setStarred(next);
    startTransition(async () => {
      await fetch(`/api/leads/${encodeURIComponent(domain)}/star`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ starred: next })
      });
    });
  }

  return (
    <div className="review">
      <textarea
        aria-label={`Review notes for ${domain}`}
        value={notes}
        onChange={(event) => setNotes(event.target.value)}
        onBlur={saveNotes}
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
        <button
          className={`iconButton star${starred ? " starred" : ""}`}
          disabled={isPending}
          onClick={toggleStar}
          title={starred ? "Unstar" : "Star — top lead"}
          type="button"
        >
          <Star size={16} />
        </button>
        <span className={`verdict ${verdict || "none"}`}>{verdict || "open"}</span>
      </div>
    </div>
  );
}
