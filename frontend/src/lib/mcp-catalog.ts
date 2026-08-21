/**
 * Curated MCP plugin catalog — the "marketplace" shown in Settings → Integrations.
 *
 * Every entry here was verified to work with our client. The transport
 * (streamable HTTP or SSE) is inferred from the URL on the backend, so
 * SSE-only servers such as Atlassian/Jira sit alongside the rest.
 * The URL is baked in so non-technical users never see it; entries with
 * `auth: "token"` show a single token field plus a direct link to the page
 * where the token is generated.
 */

import { MCP_LOGOS } from "./mcp-logos.generated";

export type McpCatalogCategory = "business" | "research" | "finance" | "developer";

/** Display order + labels for the catalog section headings. */
export const MCP_CATEGORIES: { id: McpCatalogCategory; label: string }[] = [
  { id: "business", label: "Business & work" },
  { id: "research", label: "Research & docs" },
  { id: "finance", label: "Finance & markets" },
  { id: "developer", label: "Developer" },
];

export interface McpCatalogEntry {
  /** Stable id — also used as the default connection name (slug). */
  id: string;
  /** Which section heading this card appears under. */
  category: McpCatalogCategory;
  title: string;
  /** Brand domain the color logo is keyed by (see {@link logoDataUri}). */
  domain: string;
  description: string;
  /**
   * For tokenPlacement: "url" this contains a `{token}` placeholder.
   * For auth: "personal-url" this is empty — the user pastes their own link.
   */
  url: string;
  auth: "none" | "token" | "personal-url" | "oauth";
  /**
   * Where the token goes: Authorization Bearer header (default) or embedded
   * into the URL (some providers, e.g. Alpha Vantage, take ?apikey=...).
   */
  tokenPlacement?: "header" | "url";
  /** For auth: "token" / "personal-url" — where to generate the credential. */
  tokenHelp?: { label: string; url: string };
  /** Short "what you can ask" example shown on the card. */
  example?: string;
  /** Conservative tool allowlist applied when the OAuth connection is created. */
  allowedTools?: string[];
}

/**
 * Origin (scheme + host + port) of a connection URL — used to match catalog
 * entries to connections.
 *
 * The backend stores only the origin in the plaintext `url` column that the
 * API returns (`strip_url_credentials`): a provider's secret can sit in the
 * query string *or* the path, so everything after the authority is treated as
 * credential-bearing. Catalog URLs carry a path, so a stored URL can only be
 * compared to one after both sides are reduced to their origin. Mirrors the
 * backend helper — keep the two in step.
 */
export function catalogOrigin(url: string): string {
  try {
    return new URL(url).origin;
  } catch {
    return url;
  }
}

/** What a stored connection has to carry to be identified as a catalog entry. */
export interface McpConnectionIdentity {
  name: string;
  url: string;
}

/**
 * The user's existing connection for *entry*, or null.
 *
 * The name is checked first for every entry: the catalog always creates under
 * the entry id, and the name is what the backend rejects duplicates on — a
 * card that fails to find its own connection offers a Connect that 409s.
 * Fixed-URL entries additionally match a hand-added connection by origin;
 * origin alone would be wrong for the OAuth entries, several of which share
 * `www.googleapis.com`. Personal-url entries (Zapier) have no catalog URL to
 * compare at all.
 */
export function findCatalogConnection<T extends McpConnectionIdentity>(
  connections: readonly T[],
  entry: McpCatalogEntry,
): T | null {
  const byName = connections.find((c) => c.name === entry.id) ?? null;
  if (byName || entry.auth === "oauth" || entry.auth === "personal-url") return byName;
  return connections.find((c) => catalogOrigin(c.url) === catalogOrigin(entry.url)) ?? null;
}

/**
 * True when *connection* still points at a different host than the catalog
 * now lists for it — an OAuth connection made against a superseded endpoint
 * (e.g. `mcp.googleapis.com` before the standard API roots), which the user
 * is offered a one-click re-authorization to move.
 */
export function catalogUpgradeAvailable(connection: McpConnectionIdentity): boolean {
  const entry = MCP_CATALOG.find((candidate) => candidate.id === connection.name);
  if (!entry || entry.auth !== "oauth") return false;
  return catalogOrigin(entry.url) !== catalogOrigin(connection.url);
}

/**
 * Last-resort logo source: Google's favicon service, tokenless and over HTTPS.
 * Only reached for a domain missing from {@link MCP_LOGOS} — every catalog
 * entry should be baked in, so hitting this means `bun run gen:mcp-logos`
 * needs a re-run. Not exported: nothing should request it on purpose.
 */
function faviconServiceUrl(domain: string): string {
  return `https://www.google.com/s2/favicons?sz=128&domain=${encodeURIComponent(domain)}`;
}

/**
 * Brand logo for a catalog domain, as a baked-in data URI ({@link MCP_LOGOS}).
 *
 * Used everywhere a catalog logo is shown — the Settings marketplace and the
 * demo-only MCP badge alike. Baked rather than fetched for two reasons: the
 * self-contained export has no network, and a live app shouldn't tell a third
 * party which plugins its users are looking at. Regenerate the map with
 * `bun run gen:mcp-logos` after adding a catalog entry.
 */
export function logoDataUri(domain: string): string {
  return MCP_LOGOS[domain] ?? faviconServiceUrl(domain);
}

/** Catalog id / connection name → tool prefix (mirrors the backend `_tool_prefix`). */
function toToolPrefix(name: string): string {
  return (
    name
      .toLowerCase()
      .replace(/[^a-z0-9_]/g, "_")
      .replace(/^_+|_+$/g, "") || "mcp"
  );
}

/**
 * Match a tool-call name to a known catalog server by its `{prefix}_` prefix.
 *
 * Purely static — it reads only {@link MCP_CATALOG}, never live connections — so
 * it works identically in a live chat, the `/demo` replay and the self-contained
 * export (which has no backend). Catalog-connected servers keep their id as the
 * connection name, so the tool prefix is `toToolPrefix(entry.id)`. Returns the
 * matched entry plus the prefix (longest wins), or `null` for built-in tools or
 * servers not in the catalog (custom URLs have no brand logo to show).
 */
export function matchCatalogMcpTool(
  toolName: string,
): { entry: McpCatalogEntry; prefix: string } | null {
  let best: { entry: McpCatalogEntry; prefix: string } | null = null;
  for (const entry of MCP_CATALOG) {
    const prefix = toToolPrefix(entry.id);
    if (toolName === prefix || toolName.startsWith(`${prefix}_`)) {
      if (!best || prefix.length > best.prefix.length) best = { entry, prefix };
    }
  }
  return best;
}

export const MCP_CATALOG: McpCatalogEntry[] = [
  {
    id: "gmail",
    category: "business",
    title: "Gmail",
    domain: "google.com",
    description:
      "Search and read mail, create reviewable drafts, and send approved messages through the Gmail API.",
    url: "https://gmail.googleapis.com/gmail/v1",
    auth: "oauth",
    allowedTools: [
      "get_profile",
      "create_draft",
      "send_draft",
      "send_message",
      "delete_draft",
      "get_message",
      "get_thread",
      "list_drafts",
      "list_labels",
      "search_threads",
    ],
    example: "“Draft a reply for review, then send it after I approve.”",
  },
  {
    id: "google-drive",
    category: "research",
    title: "Google Drive",
    domain: "google.com",
    description:
      "Search, read, upload, organize, share, and delete files through the standard Drive API.",
    url: "https://www.googleapis.com/drive/v3",
    auth: "oauth",
    allowedTools: [
      "download_file_content",
      "get_file_metadata",
      "get_file_permissions",
      "list_permissions",
      "list_folder_contents",
      "list_recent_files",
      "read_file_content",
      "search_files",
      "create_folder",
      "upload_file",
      "copy_file",
      "update_file",
      "move_file",
      "trash_file",
      "restore_file",
      "delete_file",
      "create_permission",
      "update_permission",
      "delete_permission",
    ],
    example: "“Find the latest machine setup document in my Drive.”",
  },
  {
    id: "google-docs",
    category: "research",
    title: "Google Docs",
    domain: "google.com",
    description:
      "Create, read, edit, format, search, and delete documents through the standard Docs API.",
    url: "https://docs.googleapis.com/v1",
    auth: "oauth",
    allowedTools: [
      "search_docs",
      "read_doc",
      "create_document",
      "insert_text",
      "append_text",
      "replace_text",
      "delete_text",
      "format_text",
      "format_paragraph",
      "delete_document",
    ],
    example: "“Summarize the troubleshooting document I shared.”",
  },
  {
    id: "google-sheets",
    category: "business",
    title: "Google Sheets",
    domain: "google.com",
    description:
      "Create, read, write, format, and organize spreadsheets through the standard Sheets API.",
    url: "https://sheets.googleapis.com/v4",
    auth: "oauth",
    allowedTools: [
      "search_spreadsheets",
      "get_spreadsheet",
      "get_values",
      "batch_get_values",
      "create_spreadsheet",
      "update_values",
      "append_values",
      "clear_values",
      "add_sheet",
      "rename_sheet",
      "delete_sheet",
      "format_range",
      "delete_spreadsheet",
    ],
    example: "“Read the open issues from my maintenance tracker.”",
  },
  {
    id: "google-slides",
    category: "research",
    title: "Google Slides",
    domain: "google.com",
    description:
      "Create, read, edit, reorder, and delete presentations through the standard Slides API.",
    url: "https://slides.googleapis.com/v1",
    auth: "oauth",
    allowedTools: [
      "search_presentations",
      "read_presentation",
      "get_page",
      "get_thumbnail",
      "create_presentation",
      "add_slide",
      "duplicate_slide",
      "move_slides",
      "delete_slide",
      "create_text_box",
      "create_image",
      "insert_text",
      "replace_text",
      "format_text",
      "delete_presentation",
    ],
    example: "“Summarize this applications training presentation.”",
  },
  {
    id: "google-calendar",
    category: "business",
    title: "Google Calendar",
    domain: "google.com",
    description: "Read calendars, events and suggested times through the standard Calendar API.",
    url: "https://www.googleapis.com/calendar/v3",
    auth: "oauth",
    allowedTools: ["get_event", "list_calendars", "list_events", "search_events", "suggest_time"],
    example: "“What customer visits do I have next week?”",
  },
  {
    id: "google-chat",
    category: "business",
    title: "Google Chat",
    domain: "google.com",
    description:
      "Manage spaces, messages, reactions, and memberships through the standard Chat API.",
    url: "https://chat.googleapis.com/v1",
    auth: "oauth",
    allowedTools: [
      "list_spaces",
      "get_space",
      "create_space",
      "update_space",
      "delete_space",
      "list_messages",
      "search_messages",
      "get_message",
      "create_message",
      "update_message",
      "delete_message",
      "list_reactions",
      "create_reaction",
      "delete_reaction",
      "list_memberships",
      "create_membership",
      "delete_membership",
    ],
    example: "“Find the discussion about that spindle alarm.”",
  },
  {
    id: "google-contacts",
    category: "business",
    title: "Google Contacts",
    domain: "google.com",
    description:
      "Manage contacts and groups, read your profile, and search a Workspace directory through the People API.",
    url: "https://people.googleapis.com/v1",
    auth: "oauth",
    allowedTools: [
      "get_user_profile",
      "list_contacts",
      "get_contact",
      "search_contacts",
      "list_other_contacts",
      "search_directory_people",
      "create_contact",
      "update_contact",
      "delete_contact",
      "list_contact_groups",
      "create_contact_group",
      "update_contact_group",
      "delete_contact_group",
      "modify_contact_group",
    ],
    example: "“Find the contact details for our regional applications engineer.”",
  },
  {
    id: "coingecko",
    category: "finance",
    title: "CoinGecko",
    domain: "coingecko.com",
    description: "Live cryptocurrency prices, market caps and trends — no account needed.",
    url: "https://mcp.api.coingecko.com/mcp",
    auth: "none",
    example: "“What's the Bitcoin price and how did it move this week?”",
  },
  {
    id: "alphavantage",
    category: "finance",
    title: "Alpha Vantage",
    domain: "alphavantage.co",
    description: "Stock quotes, forex and market data. Free API key — just enter your email.",
    url: "https://mcp.alphavantage.co/mcp?apikey={token}",
    auth: "token",
    tokenPlacement: "url",
    tokenHelp: {
      label: "Get a free API key (takes 30 seconds)",
      url: "https://www.alphavantage.co/support/#api-key",
    },
    example: "“Compare Apple and Microsoft stock over the last month.”",
  },
  {
    id: "stripe",
    category: "business",
    title: "Stripe",
    domain: "stripe.com",
    description: "Your Stripe payments, customers and invoices — for business owners.",
    url: "https://mcp.stripe.com/",
    auth: "token",
    tokenHelp: {
      label: "Get your API key from the Stripe dashboard",
      url: "https://dashboard.stripe.com/apikeys",
    },
    example: "“How many new customers did we get this month?”",
  },
  {
    id: "notion",
    category: "business",
    title: "Notion",
    domain: "notion.so",
    description: "Your Notion pages, docs and databases — sign in, no token to copy.",
    url: "https://mcp.notion.com/mcp",
    auth: "oauth",
    example: "“Summarize our product spec doc in Notion.”",
  },
  {
    id: "linear",
    category: "business",
    title: "Linear",
    domain: "linear.app",
    description: "Issues, projects and cycles in Linear — sign in with your account.",
    url: "https://mcp.linear.app/mcp",
    auth: "oauth",
    example: "“What issues are assigned to me this cycle?”",
  },
  {
    id: "jira",
    category: "business",
    title: "Jira & Confluence",
    domain: "atlassian.com",
    description:
      "Atlassian issues, boards, sprints and Confluence pages — sign in with your account.",
    url: "https://mcp.atlassian.com/v1/sse",
    auth: "oauth",
    example: "“What Jira issues are assigned to me in the current sprint?”",
  },
  {
    id: "zapier",
    category: "business",
    title: "Zapier (Slack, Sheets, Gmail…)",
    domain: "zapier.com",
    description:
      "Connect 7000+ business apps — Slack, Google Sheets, Gmail, HubSpot, Salesforce, Trello. Pick your apps on Zapier, then paste your personal link here.",
    url: "",
    auth: "personal-url",
    tokenHelp: {
      label: "Create your personal MCP link on Zapier",
      url: "https://mcp.zapier.com",
    },
    example: "“Add a row to my Google Sheet from this conversation.”",
  },
  {
    id: "exa",
    category: "research",
    title: "Exa Search",
    domain: "exa.ai",
    description:
      "Live web search and page reading — great for market research, competitor checks and fact-finding.",
    url: "https://mcp.exa.ai/mcp",
    auth: "none",
    example: "“Find recent news about our top three competitors.”",
  },
  {
    id: "deepwiki",
    category: "research",
    title: "DeepWiki",
    domain: "deepwiki.com",
    description: "Ask questions about any public GitHub repository's code and docs.",
    url: "https://mcp.deepwiki.com/mcp",
    auth: "none",
    example: "“What does the documentation of facebook/react say about hooks?”",
  },
  {
    id: "context7",
    category: "research",
    title: "Context7",
    domain: "context7.com",
    description: "Up-to-date documentation for programming libraries and frameworks.",
    url: "https://mcp.context7.com/mcp",
    auth: "none",
    example: "“How do I set up middleware in Next.js 15? Check the docs.”",
  },
  {
    id: "huggingface",
    category: "developer",
    title: "Hugging Face",
    domain: "huggingface.co",
    description: "Search models, datasets and papers on the Hugging Face Hub.",
    url: "https://huggingface.co/mcp",
    auth: "none",
    example: "“Find the most downloaded text-to-speech models.”",
  },
  {
    id: "microsoft-docs",
    category: "research",
    title: "Microsoft Learn",
    domain: "learn.microsoft.com",
    description: "Official Microsoft / Azure documentation search and code samples.",
    url: "https://learn.microsoft.com/api/mcp",
    auth: "none",
    example: "“How do I configure a managed identity for an Azure Function?”",
  },
  {
    id: "cloudflare-docs",
    category: "developer",
    title: "Cloudflare Docs",
    domain: "cloudflare.com",
    description: "Search Cloudflare documentation — Workers, Pages, DNS, security.",
    url: "https://docs.mcp.cloudflare.com/mcp",
    auth: "none",
    example: "“How do I set up a cron trigger for a Worker?”",
  },
  {
    id: "github",
    category: "developer",
    title: "GitHub",
    domain: "github.com",
    description: "Your repositories, issues and pull requests — needs a personal access token.",
    url: "https://api.githubcopilot.com/mcp/",
    auth: "token",
    tokenHelp: {
      label: "Generate a token on GitHub",
      url: "https://github.com/settings/tokens",
    },
    example: "“List open pull requests in my repository.”",
  },
];
