import { keywordsFor } from "@/lib/keywords";

export const dynamic = "force-dynamic";

export default async function KeywordsPage({ params }: { params: Promise<{ industry: string }> }) {
  const { industry } = await params;
  const { groups, count } = keywordsFor(industry);

  return (
    <section className="stack">
      <div className="pageHeader">
        <div>
          <h1>Scan Keywords</h1>
          <p>
            Newly registered domains whose name contains one of these words are pulled into the
            pipeline for geo-check and scraping. {count} keywords across {groups.length} categories.
          </p>
        </div>
      </div>

      <div className="keywordGroups">
        {groups.map((group) => (
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
