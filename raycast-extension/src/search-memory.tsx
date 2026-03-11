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
  fact: Icon.LightBulb,
  solution: Icon.Checkmark,
  code_pattern: Icon.Code,
  preference: Icon.Star,
  reference: Icon.Link,
};

function toPasteFormat(r: SearchResult): string {
  const source = r.source.charAt(0).toUpperCase() + r.source.slice(1);
  const tags = r.topic_tags.join(", ");
  const lines: string[] = [`<context source="${source}" date="${r.source_date ?? "unknown"}" topic="${tags}">`];
  if (r.conversation_summary) {
    lines.push("## Conversation Summary");
    lines.push(r.conversation_summary);
    lines.push("");
    lines.push("## Matched Knowledge");
  }
  lines.push(r.content);
  if (r.context) lines.push(`Context: ${r.context}`);
  lines.push("</context>");
  return lines.join("\n");
}

function toPasteFormatMulti(results: SearchResult[]): string {
  // Deduplicate by id, preserve order
  const seen = new Set<string>();
  const unique = results.filter((r) => {
    if (seen.has(r.id)) return false;
    seen.add(r.id);
    return true;
  });
  return unique.map(toPasteFormat).join("\n\n");
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
          <ActionPanel.Section title="Copy">
            <Action
              title="Copy as Context (with Summary)"
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
          </ActionPanel.Section>
          <ActionPanel.Section>
            <ResultActions result={result} selectedIds={new Set()} onToggleSelect={() => {}} />
          </ActionPanel.Section>
        </ActionPanel>
      }
    />
  );
}

function ResultActions({
  result,
  selectedIds,
  onToggleSelect,
}: {
  result: SearchResult;
  selectedIds: Set<string>;
  onToggleSelect: (id: string) => void;
}) {
  const isSelected = selectedIds.has(result.id);
  return (
    <>
      <Action.Push
        title="View Details & Copy"
        icon={Icon.Eye}
        shortcut={{ modifiers: [], key: "return" }}
        target={<ResultDetail result={result} />}
      />
      <Action
        title="Copy as Context"
        icon={Icon.Clipboard}
        shortcut={{ modifiers: ["cmd"], key: "return" }}
        onAction={async () => {
          await Clipboard.copy(toPasteFormat(result));
          await showHUD("Context copied ✓");
        }}
      />
      <Action
        title={isSelected ? "Deselect" : "Select for Multi-Copy"}
        icon={isSelected ? Icon.XMarkCircle : Icon.PlusCircle}
        shortcut={{ modifiers: ["cmd"], key: "d" }}
        onAction={() => onToggleSelect(result.id)}
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
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

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

  function toggleSelect(id: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function copySelected() {
    const items = results.filter((r) => selectedIds.has(r.id));
    if (items.length === 0) return;
    await Clipboard.copy(toPasteFormatMulti(items));
    await showHUD(`Copied ${items.length} context block${items.length > 1 ? "s" : ""} ✓`);
    setSelectedIds(new Set());
  }

  const selectedCount = selectedIds.size;
  const searchBarTitle = selectedCount > 0 ? `Search Memory — ${selectedCount} selected` : undefined;

  return (
    <List
      isLoading={isLoading}
      onSearchTextChange={(text) => {
        setQuery(text);
        // Clear selection when query changes
        if (text !== query) setSelectedIds(new Set());
      }}
      searchBarPlaceholder="Search your AI conversation memory…"
      navigationTitle={searchBarTitle}
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

      {/* Multi-select action bar — only shown when items are selected */}
      {selectedCount > 0 && (
        <List.Section title={`${selectedCount} selected — press ⌘⏎ on any item, or use Copy Selected below`}>
          <List.Item
            icon={{ source: Icon.Clipboard, tintColor: Color.Blue }}
            title={`Copy ${selectedCount} Selected Context Block${selectedCount > 1 ? "s" : ""}`}
            subtitle="Pastes all selected items as separate <context> blocks"
            actions={
              <ActionPanel>
                <Action
                  title={`Copy ${selectedCount} Selected`}
                  icon={Icon.Clipboard}
                  shortcut={{ modifiers: [], key: "return" }}
                  onAction={copySelected}
                />
                <Action
                  title="Clear Selection"
                  icon={Icon.XMarkCircle}
                  shortcut={{ modifiers: ["cmd"], key: "escape" }}
                  onAction={() => setSelectedIds(new Set())}
                />
              </ActionPanel>
            }
          />
        </List.Section>
      )}

      <List.Section title={results.length > 0 ? `${results.length} results` : ""}>
        {results.map((r) => {
          const category = r.category ?? "fact";
          const score = Math.round(r.similarity_score * 100);
          const tags = r.topic_tags.slice(0, 3).join(", ");
          const isSelected = selectedIds.has(r.id);

          return (
            <List.Item
              key={r.id}
              icon={
                isSelected
                  ? { source: Icon.Checkmark, tintColor: Color.Blue }
                  : { source: CATEGORY_ICONS[category] ?? Icon.Dot, tintColor: CATEGORY_COLORS[category] }
              }
              title={r.content.length > 80 ? r.content.slice(0, 80) + "…" : r.content}
              subtitle={isSelected ? `✓ selected · ${tags}` : tags}
              accessories={[
                { text: r.source_date ?? "", tooltip: "Date" },
                { tag: { value: `${score}%`, color: score >= 90 ? Color.Green : score >= 75 ? Color.Yellow : Color.SecondaryText } },
              ]}
              actions={
                <ActionPanel>
                  {selectedCount > 0 && (
                    <Action
                      title={`Copy ${selectedCount} Selected`}
                      icon={Icon.Clipboard}
                      shortcut={{ modifiers: ["cmd"], key: "return" }}
                      onAction={copySelected}
                    />
                  )}
                  <ResultActions result={r} selectedIds={selectedIds} onToggleSelect={toggleSelect} />
                </ActionPanel>
              }
            />
          );
        })}
      </List.Section>
    </List>
  );
}
