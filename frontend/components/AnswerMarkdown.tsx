"use client";

import type { ReactNode } from "react";
import type { Citation } from "@/lib/api";

interface Props {
  text: string;
  citations?: Citation[];
}

type Block =
  | { type: "paragraph"; text: string }
  | { type: "heading"; level: 2 | 3; text: string }
  | { type: "ul"; items: string[] }
  | { type: "ol"; items: string[] };

// Maps a source id ("S1") -> the Wikipedia-style display number (its position
// among the cited sources) and its url. Renumbering by cited order means the
// reader sees sequential ¹²³ that match the source cards below, never gappy
// raw ids like S4/S6.
type RefMap = Map<string, { num: number; url: string }>;

function buildRefMap(citations: Citation[]): RefMap {
  const map: RefMap = new Map();
  citations.forEach((c, i) => map.set(c.id, { num: i + 1, url: c.source_url }));
  return map;
}

function inlineNodes(text: string, keyPrefix: string, refs: RefMap): ReactNode[] {
  const nodes: ReactNode[] = [];
  // A run of one or more source markers separated only by commas/spaces is
  // captured as a single token so it renders as one tidy superscript group
  // (e.g. "¹,²,³") instead of leaving baseline commas between them.
  const pattern = /(\*\*[^*]+?\*\*|\*[^*\n]+?\*|\[S\d+\](?:\s*,?\s*\[S\d+\])*)/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let index = 0;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(text.slice(lastIndex, match.index));
    }

    const token = match[0];
    const key = `${keyPrefix}-${index}`;

    if (token.startsWith("[S")) {
      // Render the marker run as one superscript group, commas kept superscript
      // and tight. Unknown/hallucinated ids (not in refs) are dropped silently.
      const found = (token.match(/S\d+/g) ?? [])
        .map((id) => refs.get(id))
        .filter((r): r is { num: number; url: string } => Boolean(r));
      if (found.length > 0) {
        nodes.push(
          <sup key={key} className="cite-ref">
            {found.map((ref, i) => (
              <span key={i}>
                {i > 0 && ","}
                <a href={ref.url} target="_blank" rel="noreferrer" aria-label={`Kaynak ${ref.num}`}>
                  {ref.num}
                </a>
              </span>
            ))}
          </sup>
        );
      }
    } else if (token.startsWith("**")) {
      nodes.push(<strong key={key}>{inlineNodes(token.slice(2, -2), key, refs)}</strong>);
    } else {
      nodes.push(<em key={key}>{inlineNodes(token.slice(1, -1), key, refs)}</em>);
    }

    lastIndex = pattern.lastIndex;
    index += 1;
  }

  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex));
  }

  return nodes;
}

function parseBlocks(text: string): Block[] {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const blocks: Block[] = [];
  let paragraph: string[] = [];
  let list: { type: "ul" | "ol"; items: string[] } | null = null;

  function flushParagraph() {
    if (paragraph.length > 0) {
      blocks.push({ type: "paragraph", text: paragraph.join(" ") });
      paragraph = [];
    }
  }

  function flushList() {
    if (list) {
      blocks.push(list);
      list = null;
    }
  }

  for (const rawLine of lines) {
    const line = rawLine.trim();

    if (!line) {
      flushParagraph();
      flushList();
      continue;
    }

    const heading = line.match(/^(#{2,3})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      flushList();
      blocks.push({
        type: "heading",
        level: heading[1].length as 2 | 3,
        text: heading[2],
      });
      continue;
    }

    const unordered = line.match(/^[-*]\s+(.+)$/);
    if (unordered) {
      flushParagraph();
      if (!list || list.type !== "ul") {
        flushList();
        list = { type: "ul", items: [] };
      }
      list.items.push(unordered[1]);
      continue;
    }

    const ordered = line.match(/^\d+\.\s+(.+)$/);
    if (ordered) {
      flushParagraph();
      if (!list || list.type !== "ol") {
        flushList();
        list = { type: "ol", items: [] };
      }
      list.items.push(ordered[1]);
      continue;
    }

    flushList();
    paragraph.push(line);
  }

  flushParagraph();
  flushList();

  return blocks;
}

export default function AnswerMarkdown({ text, citations }: Props) {
  const blocks = parseBlocks(text);
  const refs = buildRefMap(citations ?? []);

  return (
    <div className="answer-markdown">
      {blocks.map((block, index) => {
        const key = `block-${index}`;

        if (block.type === "heading") {
          const Tag = block.level === 2 ? "h2" : "h3";
          return <Tag key={key}>{inlineNodes(block.text, key, refs)}</Tag>;
        }

        if (block.type === "ul") {
          return (
            <ul key={key}>
              {block.items.map((item, itemIndex) => (
                <li key={`${key}-${itemIndex}`}>{inlineNodes(item, `${key}-${itemIndex}`, refs)}</li>
              ))}
            </ul>
          );
        }

        if (block.type === "ol") {
          return (
            <ol key={key}>
              {block.items.map((item, itemIndex) => (
                <li key={`${key}-${itemIndex}`}>{inlineNodes(item, `${key}-${itemIndex}`, refs)}</li>
              ))}
            </ol>
          );
        }

        return <p key={key}>{inlineNodes(block.text, key, refs)}</p>;
      })}
    </div>
  );
}
