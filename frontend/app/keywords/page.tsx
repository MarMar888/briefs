import { KEYWORD_GROUPS, KEYWORD_COUNT } from "@/lib/keywords";

export const metadata = {
  title: "Keywords · Briefs",
};

export default function KeywordsPage() {
  return (
    <section className="stack">
      <div className="pageHeader">
        <div>
          <h1>Scan Keywords</h1>
          <p>
            Newly registered domains whose name contains one of these words are pulled into the
            pipeline for geo-check and scraping. {KEYWORD_COUNT} keywords across{" "}
            {KEYWORD_GROUPS.length} categories.
          </p>
        </div>
      </div>

      <div className="keywordGroups">
        {KEYWORD_GROUPS.map((group) => (
          <div key={group.label} className="keywordGroup">
            <h2>
              {group.label}
              <span>{group.words.length}</span>
            </h2>
            <div className="tags">
              {group.words.map((word) => (
                <span key={word}>{word}</span>
              ))}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
