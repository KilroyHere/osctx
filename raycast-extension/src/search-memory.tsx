import {
  Action,
  ActionPanel,
  Color,
  Detail,
  Icon,
  List,
  Toast,
  showHUD,
  showToast,
  Clipboard,
  getPreferenceValues,
} from "@raycast/api";
import { useFetch } from "@raycast/utils";
import { useState } from "react";

interface Preferences {
  daemonUrl: string;
}

interface SearchResult {
  id: string;
  content: string;
  category: string | null;
  topic_tags: string[];
  source: string;
  source_date: string | null;
  source_url: string | null;
  confidence: number | null;
  similarity_score: number;
  context: string | null;
  conversation_id: string | null;
  conversation_summary: string | null;
}

interface SearchResponse {
  results: SearchResult[];
  query: string;
}

const CATEGORY_COLORS: Record<string, Color> = {
  decision: Color.Blue,
  fact: Color.Green,
  solution: Color.Yellow,
  code_pattern: Color.Purple,
  preference: Color.Orange,
  reference: Color.Magenta,
};

const CATEGORY_ICONS: Record<string, Icon> = {
  decision: Icon.BulletPoints,
  fact: Icon.Lightbulb,
  solution: Icon.Checkmark,
  code_pattern: Icon.Code,
  preference: Icon.Star,
  reference: Icon.Link,
};

function toPasteFormat(r: SearchResult): string {
  const source = r.source.charAt(0).toUpperCase() + r.source.slice(1);
  const tags = r.topic_tags.join(", ");
  let text = `<context source="${source}" date="${r.source_date ?? "unknown"}" topic="${tags}">\n${r.content}`;
  if (r.context) text += `\nContext: ${r.context}`;
  text += "\n</context>";
  return text;
}

function ResultDetail({ result }: { result: SearchResult }) {
  const markdown = [
    result.conversation_summary ? "## Conversation Summary" : "",
    result.conversation_summary ?? "",
    result.conversation_summary ? "\n---\n" : "",
    `## Matched Knowledge`,
    `**[${result.category ?? "fact"}]** ${result.content}`,
    "",
    result.context ? `*${result.context}*` : "",
    "",
    "---",
    "",
    "**Paste format:**",
    "```xml",
    toPasteFormat(result),
    "```",
  ]
    .filter((l) => l !== undefined)
    .join("\n");

  return (
    <Detail
      markdown={markdown}
      metadata={
        <Detail.Metadata>
          <Detail.Metadata.Label title="Category" text={result.category ?? "—"} />
          <Detail.Metadata.Label title="Source" text={result.source} />
          <Detail.Metadata.Label title="Date" text={result.source_date ?? "—"} />
          <Detail.Metadata.Label
            title="Confidence"
            text={result.confidence ? `${Math.round(result.confidence * 100)}%` : "—"}
          />
          <Detail.Metadata.Label
            title="Similarity"
            text={`${Math.round(result.similarity_score * 100)}%`}
          />
          <Detail.Metadata.Separator />
          <Detail.Metadata.TagList title="Tags">
            {result.topic_tags.map((tag) => (
              <Detail.Metadata.TagList.Item key={tag} text={tag} />
            ))}
          </Detail.Metadata.TagList>
        </Detail.Metadata>
      }
      actions={
        <ActionPanel>
          <ResultActions result={result} />
        </ActionPanel>
      }
    />
  );
}

function ResultActions({ result }: { result: SearchResult }) {
  return (
    <>
      <Action
        title="Copy as Context"
        icon={Icon.Clipboard}
        shortcut={{ modifiers: [], key: "return" }}
        onAction={async () => {
          await Clipboard.copy(toPasteFormat(result));
          await showHUD("Context copied ✓");
        }}
      />
      <Action
        title="Copy Content Only"
        icon={Icon.Text}
        shortcut={{ modifiers: ["cmd"], key: "c" }}
        onAction={async () => {
          await Clipboard.copy(result.content);
          await showHUD("Content copied ✓");
        }}
      />
      <Action.Push
        title="View Details"
        icon={Icon.Eye}
        shortcut={{ modifiers: ["cmd"], key: "return" }}
        target={<ResultDetail result={result} />}
      />
      {result.source_url && (
        <Action.OpenInBrowser
          title="Open Source"
          url={result.source_url}
          shortcut={{ modifiers: ["cmd"], key: "o" }}
        />
      )}
    </>
  );
}

export default function SearchMemory() {
  const { daemonUrl } = getPreferenceValues<Preferences>();
  const [query, setQuery] = useState("");

  const url = query.trim()
    ? `${daemonUrl}/search?q=${encodeURIComponent(query)}&limit=10`
    : null;

  const { isLoading, data, error } = useFetch<SearchResponse>(url ?? "", {
    execute: !!url,
    keepPreviousData: true,
    onError: () => {
      showToast({
        style: Toast.Style.Failure,
        title: "OSCTX daemon offline",
        message: `Could not reach ${daemonUrl}. Run: osctx install`,
      });
    },
  });

  const results = data?.results ?? [];

  return (
    <List
      isLoading={isLoading}
      onSearchTextChange={setQuery}
      searchBarPlaceholder="Search your AI conversation memory…"
      throttle
    >
      {!query.trim() && (
        <List.EmptyView
          icon={Icon.MagnifyingGlass}
          title="Search your memory"
          description="Type anything — topics, decisions, solutions, code patterns"
        />
      )}

      {query.trim() && !isLoading && results.length === 0 && !error && (
        <List.EmptyView
          icon={Icon.XMarkCircle}
          title="No results"
          description={`Nothing found for "${query}"`}
        />
      )}

      {error && (
        <List.EmptyView
          icon={Icon.ExclamationMark}
          title="Daemon offline"
          description="Start with: osctx install"
        />
      )}

      <List.Section title={results.length > 0 ? `${results.length} results` : ""}>
        {results.map((r) => {
          const category = r.category ?? "fact";
          const score = Math.round(r.similarity_score * 100);
          const tags = r.topic_tags.slice(0, 3).join(", ");

          return (
            <List.Item
              key={r.id}
              icon={{ source: CATEGORY_ICONS[category] ?? Icon.Dot, tintColor: CATEGORY_COLORS[category] }}
              title={r.content.length > 80 ? r.content.slice(0, 80) + "…" : r.content}
              subtitle={tags}
              accessories={[
                { text: r.source_date ?? "", tooltip: "Date" },
                { tag: { value: `${score}%`, color: score >= 90 ? Color.Green : score >= 75 ? Color.Yellow : Color.SecondaryText } },
              ]}
              actions={
                <ActionPanel>
                  <ResultActions result={r} />
                </ActionPanel>
              }
            />
          );
        })}
      </List.Section>
    </List>
  );
}
