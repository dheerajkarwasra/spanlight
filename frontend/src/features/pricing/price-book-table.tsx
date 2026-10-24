import { Search } from "lucide-react";
import { useState } from "react";

import { DataTable, type DataColumn } from "@/components/data-table";
import { ErrorState } from "@/components/error-state";
import { SectionCard } from "@/components/section-card";
import { TileList } from "@/components/tile-list";
import { Input } from "@/components/ui/input";
import { UnknownValue } from "@/components/unknown-value";
import type { Price } from "@/lib/api";
import { formatInteger } from "@/lib/format";

import { filterBook, formatRate, latestByPattern } from "./price-book";
import { usePricesQuery } from "./pricing-queries";

const NO_RATE = "No separate rate";

function Rate({ value }: { value: string | null }) {
  const text = formatRate(value);
  return text === null ? <UnknownValue reason={NO_RATE} /> : <>{text}</>;
}

/** Rows of one pattern's higher tiers price prompts of at least that many tokens. */
function tierNote(row: Price): string | null {
  return row.min_prompt_tokens > 0
    ? `Prompts ≥ ${formatInteger(row.min_prompt_tokens)} tokens`
    : null;
}

const day = (row: Price) => row.effective_from.slice(0, 10);

function rate(id: string, header: string, pick: (row: Price) => string | null): DataColumn<Price> {
  return { id, header, width: "110px", align: "end", cell: (row) => <Rate value={pick(row)} /> };
}

const COLUMNS: DataColumn<Price>[] = [
  {
    id: "pattern",
    header: "Model pattern",
    width: "minmax(200px,1fr)",
    sticky: true,
    cell: (row) => (
      <span className="flex min-w-0 flex-col">
        <span className="truncate font-mono text-[13px]" title={row.model_pattern}>
          {row.model_pattern}
        </span>
        {tierNote(row) ? (
          <span className="text-xs text-muted-foreground">{tierNote(row)}</span>
        ) : null}
      </span>
    ),
  },
  // Pattern first: the sticky column must have no scrolling column on its left.
  { id: "provider", header: "Provider", width: "110px", cell: (row) => row.provider },
  rate("input", "Input", (row) => row.input_per_mtok),
  rate("output", "Output", (row) => row.output_per_mtok),
  rate("cache_read", "Cache read", (row) => row.cached_input_per_mtok),
  rate("cache_write_5m", "Write 5 m", (row) => row.cache_write_5m_per_mtok),
  rate("cache_write_1h", "Write 1 h", (row) => row.cache_write_1h_per_mtok),
  {
    id: "effective_from",
    header: "Effective from",
    width: "130px",
    align: "end",
    cell: (row) => <span className="font-mono text-[13px] text-muted-foreground">{day(row)}</span>,
  },
  { id: "version", header: "Version", width: "90px", align: "end", cell: (row) => row.version },
];

const rowKey = (row: Price) => `${row.provider}/${row.model_pattern}/${row.min_prompt_tokens}`;

/** "In $3.00 · out $15.00 · cache read $0.30" for a phone tile (plain text, no tooltips). */
function tileRates(row: Price): string {
  const cached = formatRate(row.cached_input_per_mtok);
  const base = `In ${formatRate(row.input_per_mtok)} · out ${formatRate(row.output_per_mtok)}`;
  return cached === null ? base : `${base} · cache read ${cached}`;
}

/**
 * Spec ruling 8 "Price book": the newest version of every pattern in the shared table, searched
 * by provider or pattern. The shared table from `md`, tiles below.
 */
export function PriceBookTable() {
  const query = usePricesQuery();
  const [search, setSearch] = useState("");
  const latest = latestByPattern(query.data ?? []);
  const rows = filterBook(latest, search);
  const empty = (
    <p className="rounded-tile bg-surface-muted p-4 text-sm text-muted-foreground">
      {search.trim() === "" ? "The price book is empty." : `No price matches “${search.trim()}”.`}
    </p>
  );

  return (
    <SectionCard
      title="Price book"
      description="Rates per 1M tokens, the newest version of each pattern."
      actions={
        <div className="relative w-full sm:w-72">
          <Search
            aria-hidden
            className="pointer-events-none absolute top-1/2 left-3.5 size-4 -translate-y-1/2 text-muted-foreground"
          />
          <Input
            type="search"
            aria-label="Search provider or model"
            placeholder="Search provider or model"
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
            }}
            className="pl-10"
          />
        </div>
      }
      className="gap-4"
    >
      {query.isError && query.data === undefined ? (
        <ErrorState
          compact
          error={query.error}
          title="Couldn't load the price book"
          onRetry={() => {
            void query.refetch();
          }}
        />
      ) : (
        <>
          <DataTable<Price>
            id="price-book"
            label="Price book"
            columns={COLUMNS}
            rows={rows}
            rowKey={rowKey}
            loading={query.isPending}
            empty={empty}
            footer={
              <p className="px-4 py-3 text-xs text-muted-foreground">
                {rows.length} of {latest.length} prices
              </p>
            }
          />
          {!query.isPending && rows.length === 0 ? <div className="md:hidden">{empty}</div> : null}
          <TileList label="Price book" className="md:hidden">
            {rows.map((row) => (
              <li
                key={rowKey(row)}
                className="flex flex-col gap-0.5 rounded-tile bg-surface-muted px-4 py-3 text-sm"
              >
                <p className="[overflow-wrap:anywhere]">
                  {row.provider}{" "}
                  <span className="font-mono text-[13px] font-semibold">{row.model_pattern}</span>
                </p>
                <p className="tabular">{tileRates(row)}</p>
                <p className="text-xs text-muted-foreground">
                  {[`Effective ${day(row)}`, row.version, tierNote(row)]
                    .filter(Boolean)
                    .join(" · ")}
                </p>
              </li>
            ))}
          </TileList>
        </>
      )}
    </SectionCard>
  );
}
