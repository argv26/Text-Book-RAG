// Client application logic for TextBook RAG Web Interface (Light Theme)

const STARTER_PROMPTS = [
  {
    label: "Thyroid Hormone Synthesis",
    query: "How is thyroid hormone synthesized and regulated, and what are the main clinical signs of hypothyroidism?"
  },
  {
    label: "Femoral Triangle Anatomy",
    query: "What are the boundaries, contents, and clinical significance of the femoral triangle?"
  },
  {
    label: "Adrenal Cortex Steroidogenesis",
    query: "Explain the zones of the adrenal cortex and the specific hormones synthesized in each zone."
  },
  {
    label: "Brachial Plexus Organization",
    query: "Describe the roots, trunks, divisions, cords, and branches of the brachial plexus."
  }
];

// App State
let sessionId = "session_" + Math.random().toString(36).substring(2, 10);
let activeBookSlug = "";
let citationsCache = new Map(); // citation_id -> citationRecord
let isAwaitingResponse = false;

// DOM Elements
const messagesContainer = document.getElementById("messages-container");
const heroWelcome = document.getElementById("hero-welcome");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const btnSend = document.getElementById("btn-send");
const btnNewChat = document.getElementById("btn-new-chat");
const bookSelect = document.getElementById("book-select");
const libraryList = document.getElementById("library-list");
const starterPromptList = document.getElementById("starter-prompt-list");
const heroCards = document.getElementById("hero-cards");
const activeBookIndicator = document.getElementById("active-book-indicator");
const evidenceDrawer = document.getElementById("evidence-drawer");
const drawerBackdrop = document.getElementById("drawer-backdrop");
const drawerContent = document.getElementById("drawer-content");
const btnCloseDrawer = document.getElementById("btn-close-drawer");
const btnToggleEvidence = document.getElementById("btn-toggle-evidence");
const evidenceCountBadge = document.getElementById("evidence-count-badge");

// Initialize application
document.addEventListener("DOMContentLoaded", () => {
  setupMarkdown();
  fetchBooks();
  renderStarterPrompts();
  bindEvents();
});

function setupMarkdown() {
  if (window.marked) {
    marked.setOptions({
      breaks: true,
      gfm: true
    });
  }
}

// Fetch library catalogue
async function fetchBooks() {
  try {
    const res = await fetch("/api/books");
    const data = await res.json();
    if (data.books && data.books.length > 0) {
      bookSelect.innerHTML = '<option value="">All Ingested Volumes (Auto-Route)</option>';
      libraryList.innerHTML = "";

      data.books.forEach(b => {
        // Dropdown option
        const opt = document.createElement("option");
        opt.value = b.slug;
        opt.textContent = `${b.title} (${b.total_pages} pp.)`;
        bookSelect.appendChild(opt);

        // Sidebar catalogue card
        const card = document.createElement("div");
        card.className = "p-2.5 rounded-lg bg-slate-50 border border-slate-200 hover:border-slate-300 transition shadow-xs";
        card.innerHTML = `
          <div class="font-semibold text-slate-800 truncate" title="${b.title}">${b.title}</div>
          <div class="flex items-center justify-between text-[11px] text-slate-500 mt-1">
            <span>${b.total_pages.toLocaleString()} pages</span>
            <span class="text-blue-600 font-mono font-medium">Indexed</span>
          </div>
        `;
        libraryList.appendChild(card);
      });
    }
  } catch (err) {
    console.error("Failed to load books:", err);
    libraryList.innerHTML = '<div class="text-xs text-rose-500 font-medium">Error loading library</div>';
  }
}

// Render starter prompts
function renderStarterPrompts() {
  starterPromptList.innerHTML = "";
  heroCards.innerHTML = "";

  STARTER_PROMPTS.forEach(p => {
    // Sidebar small prompt
    const btn = document.createElement("button");
    btn.className = "w-full text-left p-2 rounded-lg bg-slate-50 hover:bg-slate-100 border border-slate-200 hover:border-blue-300 text-xs font-medium text-slate-700 transition truncate shadow-2xs";
    btn.textContent = p.label;
    btn.addEventListener("click", () => submitQuery(p.query));
    starterPromptList.appendChild(btn);

    // Hero card
    const card = document.createElement("button");
    card.className = "p-4 rounded-xl bg-white hover:bg-blue-50/40 border border-slate-200 hover:border-blue-300 text-left transition duration-200 group flex flex-col justify-between shadow-sm hover:shadow";
    card.innerHTML = `
      <div class="font-bold text-xs text-slate-800 group-hover:text-blue-600 transition">${p.label}</div>
      <div class="text-[11px] text-slate-600 line-clamp-2 mt-1.5 leading-relaxed">${p.query}</div>
      <div class="mt-3 text-[10px] text-blue-600 font-bold flex items-center gap-1">
        <span>Ask question</span>
        <span>→</span>
      </div>
    `;
    card.addEventListener("click", () => submitQuery(p.query));
    heroCards.appendChild(card);
  });
}

// Event Bindings
function bindEvents() {
  chatForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const query = chatInput.value.trim();
    if (query) submitQuery(query);
  });

  chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      const query = chatInput.value.trim();
      if (query) submitQuery(query);
    }
  });

  // Auto-resize input
  chatInput.addEventListener("input", () => {
    chatInput.style.height = "auto";
    chatInput.style.height = Math.min(chatInput.scrollHeight, 160) + "px";
  });

  // Book select change
  bookSelect.addEventListener("change", (e) => {
    activeBookSlug = e.target.value;
    activeBookIndicator.textContent = activeBookSlug ? e.target.options[e.target.selectedIndex].text.split(" (")[0] : "All Textbooks";
  });

  // New Chat
  btnNewChat.addEventListener("click", resetSession);
  window.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      resetSession();
    }
  });

  // Drawer handlers
  btnCloseDrawer.addEventListener("click", closeDrawer);
  drawerBackdrop.addEventListener("click", closeDrawer);
  btnToggleEvidence.addEventListener("click", () => {
    if (evidenceDrawer.classList.contains("translate-x-full")) {
      openDrawer();
    } else {
      closeDrawer();
    }
  });

  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeDrawer();
  });
}

function resetSession() {
  sessionId = "session_" + Math.random().toString(36).substring(2, 10);
  citationsCache.clear();
  updateEvidenceCount();
  messagesContainer.innerHTML = "";
  messagesContainer.appendChild(heroWelcome);
  heroWelcome.classList.remove("hidden");
  chatInput.value = "";
  chatInput.style.height = "auto";
  chatInput.focus();
}

function openDrawer() {
  evidenceDrawer.classList.remove("translate-x-full");
  drawerBackdrop.classList.remove("opacity-0", "pointer-events-none");
}

function closeDrawer() {
  evidenceDrawer.classList.add("translate-x-full");
  drawerBackdrop.classList.add("opacity-0", "pointer-events-none");
}

function updateEvidenceCount() {
  evidenceCountBadge.textContent = citationsCache.size;
}

// Send Query
async function submitQuery(queryText) {
  if (isAwaitingResponse) return;
  isAwaitingResponse = true;
  btnSend.disabled = true;

  // Hide hero if visible
  if (!heroWelcome.classList.contains("hidden")) {
    heroWelcome.classList.add("hidden");
  }

  // Append user bubble
  appendUserMessage(queryText);
  chatInput.value = "";
  chatInput.style.height = "auto";

  // Append typing indicator
  const typingIndicator = appendTypingIndicator();
  scrollToBottom();

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionId,
        message: queryText,
        book_slug: activeBookSlug || null
      })
    });

    if (!res.ok) {
      throw new Error(`Server returned ${res.status}: ${res.statusText}`);
    }

    const data = await res.json();
    typingIndicator.remove();

    // Cache citations
    if (data.citations) {
      data.citations.forEach(c => {
        citationsCache.set(String(c.citation_id), c);
      });
      updateEvidenceCount();
    }

    // Append Assistant response
    appendAssistantMessage(data);
    scrollToBottom();

  } catch (err) {
    typingIndicator.remove();
    appendErrorMessage(err.message);
  } finally {
    isAwaitingResponse = false;
    btnSend.disabled = false;
    chatInput.focus();
  }
}

// Append User Message Bubble
function appendUserMessage(text) {
  const wrapper = document.createElement("div");
  wrapper.className = "flex justify-end";
  wrapper.innerHTML = `
    <div class="max-w-2xl bg-blue-600 text-white rounded-2xl rounded-tr-xs px-4 py-3 shadow-md text-sm leading-relaxed whitespace-pre-wrap font-sans">
      ${escapeHtml(text)}
    </div>
  `;
  messagesContainer.appendChild(wrapper);
}

// Append Typing Indicator
function appendTypingIndicator() {
  const wrapper = document.createElement("div");
  wrapper.className = "flex items-start gap-3";
  wrapper.innerHTML = `
    <div class="w-8 h-8 rounded-lg bg-blue-50 border border-blue-200 flex items-center justify-center flex-shrink-0 text-sm shadow-xs">
      🩺
    </div>
    <div class="bg-white border border-slate-200 rounded-2xl rounded-tl-xs px-4 py-3 shadow-sm flex items-center gap-2">
      <div class="w-2 h-2 rounded-full bg-blue-500 animate-bounce"></div>
      <div class="w-2 h-2 rounded-full bg-blue-400 animate-bounce [animation-delay:0.2s]"></div>
      <div class="w-2 h-2 rounded-full bg-blue-300 animate-bounce [animation-delay:0.4s]"></div>
      <span class="text-xs text-slate-500 ml-2 font-medium">Searching textbook structure & chunks...</span>
    </div>
  `;
  messagesContainer.appendChild(wrapper);
  return wrapper;
}

// Append Assistant Message with Tool Pills, Clickable Citations, and Follow-ups
function appendAssistantMessage(data) {
  const wrapper = document.createElement("div");
  wrapper.className = "flex items-start gap-3";

  // Format markdown reply and convert citations [1], [2] to interactive clickable pills
  let formattedHtml = window.marked ? marked.parse(data.reply) : data.reply;
  formattedHtml = formatCitationBadges(formattedHtml);

  // Tool pills HTML
  let toolsHtml = "";
  if (data.tool_calls && data.tool_calls.length > 0) {
    const pills = data.tool_calls.map(tc => {
      return `<div class="tool-pill"><span>⚡</span><span>${escapeHtml(tc.summary || tc.name)}</span></div>`;
    }).join("");
    toolsHtml = `<div class="flex flex-wrap gap-1.5 mb-3">${pills}</div>`;
  }

  // Follow-up questions HTML
  let followUpsHtml = "";
  if (data.follow_ups && data.follow_ups.length > 0) {
    const chips = data.follow_ups.map(q => {
      return `<button class="follow-up-chip" data-question="${escapeHtml(q)}"><span>✨</span><span>${escapeHtml(q)}</span></button>`;
    }).join("");
    followUpsHtml = `
      <div class="mt-4 pt-3 border-t border-slate-100">
        <div class="text-[11px] font-bold uppercase tracking-wider text-slate-500 mb-2">Explore Related Clinical Questions</div>
        <div class="flex flex-col sm:flex-row flex-wrap gap-2">${chips}</div>
      </div>
    `;
  }

  wrapper.innerHTML = `
    <div class="w-8 h-8 rounded-lg bg-blue-50 border border-blue-200 flex items-center justify-center flex-shrink-0 text-sm shadow-xs">
      🩺
    </div>
    <div class="flex-1 max-w-3xl bg-white border border-slate-200 rounded-2xl rounded-tl-xs px-5 py-4 shadow-sm space-y-2">
      ${toolsHtml}
      <div class="prose-custom">
        ${formattedHtml}
      </div>
      ${followUpsHtml}
    </div>
  `;

  // Attach citation click events
  wrapper.querySelectorAll(".citation-badge").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      const citId = btn.getAttribute("data-cit");
      showCitationInDrawer(citId);
    });
  });

  // Attach follow-up question click events
  wrapper.querySelectorAll(".follow-up-chip").forEach(btn => {
    btn.addEventListener("click", () => {
      const q = btn.getAttribute("data-question");
      if (q) submitQuery(q);
    });
  });

  messagesContainer.appendChild(wrapper);
}

// Replace [1], [2] in HTML with clickable badge buttons
function formatCitationBadges(html) {
  return html.replace(/\[(\d+)\]/g, (match, p1) => {
    return `<button type="button" class="citation-badge" data-cit="${p1}" title="Click to view citing evidence">[${p1}]</button>`;
  });
}

// Inspect citation in Right Evidence Drawer
async function showCitationInDrawer(citId) {
  const cit = citationsCache.get(String(citId));
  if (!cit) {
    drawerContent.innerHTML = `
      <div class="p-4 bg-white rounded-xl border border-slate-200 text-xs text-slate-500">
        Citation [${citId}] details not found in cache.
      </div>
    `;
    openDrawer();
    return;
  }

  // Display initial cached metadata
  const bookName = cit.book_slug.replace(/_/g, " ").replace(/-/g, " ").toUpperCase();
  drawerContent.innerHTML = `
    <div class="space-y-4 text-xs">
      <!-- Header Badge -->
      <div class="flex items-center justify-between">
        <span class="px-2.5 py-1 rounded-full bg-blue-50 text-blue-700 border border-blue-200 font-bold font-mono">
          Citation [${cit.citation_id}]
        </span>
        <span class="text-slate-500 font-mono text-[11px]">${cit.chunk_id}</span>
      </div>

      <!-- Source Book -->
      <div class="bg-white p-3.5 rounded-xl border border-slate-200 shadow-2xs">
        <div class="text-[10px] uppercase font-bold text-slate-400 mb-1">Textbook Volume</div>
        <div class="font-semibold text-slate-800">${bookName}</div>
      </div>

      <!-- Hierarchy / Heading Path -->
      <div class="bg-white p-3.5 rounded-xl border border-slate-200 shadow-2xs">
        <div class="text-[10px] uppercase font-bold text-slate-400 mb-1">Section Path</div>
        <div class="text-blue-700 font-semibold leading-relaxed">${escapeHtml(cit.heading_path || "Chapter Subsection")}</div>
      </div>

      <!-- Page info -->
      <div class="grid grid-cols-2 gap-2">
        <div class="bg-white p-3 rounded-xl border border-slate-200 shadow-2xs">
          <div class="text-[10px] uppercase font-bold text-slate-400">Printed Page</div>
          <div class="text-sm font-bold text-slate-900 mt-0.5">p. ${cit.page_start}${cit.page_start !== cit.page_end ? `-${cit.page_end}` : ''}</div>
        </div>
        <div class="bg-white p-3 rounded-xl border border-slate-200 shadow-2xs">
          <div class="text-[10px] uppercase font-bold text-slate-400">Section ID</div>
          <div class="text-sm font-bold text-slate-900 mt-0.5">#${cit.section_id}</div>
        </div>
      </div>

      <!-- Passage Text -->
      <div class="bg-white p-4 rounded-xl border border-slate-200 shadow-2xs space-y-2">
        <div class="flex items-center justify-between text-[10px] uppercase font-bold text-slate-400">
          <span>Verified Passage Excerpt</span>
          <span id="drawer-chunk-type" class="text-slate-500 font-mono">Loading full text...</span>
        </div>
        <div id="drawer-chunk-text" class="text-slate-700 leading-relaxed font-sans text-xs max-h-80 overflow-y-auto whitespace-pre-wrap bg-slate-50 p-3 rounded-lg border border-slate-200">
          Fetching chunk details from database...
        </div>
      </div>
    </div>
  `;

  openDrawer();

  // Fetch full chunk details from /api/chunk/{chunk_id}
  try {
    const res = await fetch(`/api/chunk/${cit.chunk_id}`);
    if (res.ok) {
      const chunkData = await res.json();
      const textEl = document.getElementById("drawer-chunk-text");
      const typeEl = document.getElementById("drawer-chunk-type");
      if (textEl && chunkData.text) {
        textEl.textContent = chunkData.text;
      }
      if (typeEl && chunkData.content_type) {
        typeEl.textContent = `${chunkData.content_type.toUpperCase()} • ${chunkData.token_count} TOKENS`;
      }
    }
  } catch (err) {
    console.error("Failed to load chunk text:", err);
  }
}

// Append Error Bubble
function appendErrorMessage(msg) {
  const wrapper = document.createElement("div");
  wrapper.className = "flex justify-center";
  wrapper.innerHTML = `
    <div class="max-w-md bg-rose-50 border border-rose-200 text-rose-700 text-xs rounded-xl px-4 py-2.5 text-center font-medium shadow-xs">
      ⚠️ ${escapeHtml(msg)}
    </div>
  `;
  messagesContainer.appendChild(wrapper);
}

function scrollToBottom() {
  messagesContainer.scrollTo({
    top: messagesContainer.scrollHeight,
    behavior: "smooth"
  });
}

function escapeHtml(str) {
  if (!str) return "";
  return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}
