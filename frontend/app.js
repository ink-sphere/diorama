import { observeIngestion } from "./trace.js";

const element = (id) => document.getElementById(id);
const state = {
  books: [],
  readerId: "",
  view: "",
  version: 0,
  timer: null,
  pages: [],
  page: 0,
};
const labels = {
  queued: "Queued",
  preparing: "Preparing",
  needs_account: "ChatGPT connection needed",
  failed: "Needs attention",
  ready: "Ready to read",
  removing: "Removing from library",
};
let uploading = false;
let pageRequest = 0;

async function api(path, options = {}) {
  const response = await fetch("/api/" + path, {
    ...options,
    headers: { "X-Diorama-Request": "1", ...options.headers },
    cache: "no-store",
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    const failure = new Error(
      typeof error.detail === "string"
        ? error.detail
        : "Diorama could not complete the request.",
    );
    failure.status = response.status;
    throw failure;
  }
  return response.status === 204 ? null : response.json();
}

function message(id, text = "") {
  const node = element(id);
  node.textContent = text;
  node.hidden = !text;
}

function savedSection(id) {
  const value = Number(localStorage.getItem("diorama:section:" + id));
  return Number.isInteger(value) && value >= 0 ? value : 0;
}

function make(tag, text, className) {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}

function button(label, action) {
  const node = make("button", label);
  node.onclick = () =>
    action().catch((error) => message("error", error.message));
  return node;
}

function schedule(version, callback, ms = 1600) {
  clearTimeout(state.timer);
  state.timer = setTimeout(() => {
    if (version === state.version)
      callback(version).catch((error) => {
        if (state.version === version) {
          message("error", error.message);
          schedule(version, callback, 3500);
        }
      });
  }, ms);
}

const observer = observeIngestion({
  api,
  element,
  make,
  onMissing(book) {
    if (
      followed?.id === book.id &&
      followed.registration_seq === book.registration_seq
    )
      follow(null);
  },
});
let followed = null;
let readerBook = null;
try {
  const saved = JSON.parse(localStorage.getItem("diorama:followed"));
  if (/^[a-f0-9]{64}$/.test(saved?.id)) followed = saved;
} catch {
  localStorage.removeItem("diorama:followed");
}

function follow(book) {
  followed = book
    ? {
        id: book.id,
        title: book.title,
        registration_seq: book.registration_seq,
      }
    : null;
  if (followed)
    localStorage.setItem(
      "diorama:followed",
      JSON.stringify({
        id: followed.id,
        registration_seq: followed.registration_seq,
      }),
    );
  else localStorage.removeItem("diorama:followed");
  updateFooterTrace();
}

function updateFooterTrace() {
  const trigger = element("open-trace");
  const book = followed || (state.view === "reader" ? readerBook : null);
  trigger.hidden = state.view === "library" || !book;
  trigger.setAttribute(
    "aria-label",
    book?.title ? "Agent trace for " + book.title : "Agent trace",
  );
  trigger.onclick = () => observer.open(book, trigger);
}

function shelfBook(book) {
  const row = make("article", "", "shelf-book");
  row.dataset.id = book.id;
  row.dataset.registration = book.registration_seq;
  const jacket = book.has_cover
    ? document.createElement("img")
    : make("span", book.title.slice(0, 1));
  jacket.className = "jacket";
  jacket.setAttribute("aria-hidden", "true");
  if (book.has_cover) {
    jacket.src = "/api/books/" + book.id + "/cover";
    jacket.alt = "";
    jacket.loading = "lazy";
  }
  const info = make("div", "", "book-info");
  const title = make("h3", book.title, "book-title");
  title.id = "title-" + book.id;
  row.setAttribute("aria-labelledby", title.id);
  info.append(title);
  if (book.authors.length)
    info.append(make("p", book.authors.join(", "), "book-author"));
  const primary = make("button", "", "book-action");
  primary.onclick = async () => {
    const current = state.books.find((item) => item.id === book.id);
    if (current?.status === "ready") location.hash = "reader/" + book.id;
    else if (current?.status === "needs_account") location.hash = "settings";
    else if (current?.status === "failed") {
      primary.disabled = true;
      try {
        await api("books/" + book.id + "/retry", { method: "POST" });
        if (state.view === "library") await refreshBooks(state.version);
      } catch (error) {
        message("error", error.message);
      } finally {
        primary.disabled = false;
      }
    }
  };
  const secondary = make("div", "", "book-links");
  const trace = make("button", "Agent trace", "trace-link");
  trace.setAttribute("aria-haspopup", "dialog");
  trace.setAttribute("aria-label", "Agent trace for " + book.title);
  trace.onclick = () => observer.open(book, trace);
  const remove = make("button", "Remove from library", "shelf-remove");
  remove.onclick = () => openRemoval(book.id, remove);
  secondary.append(trace, remove);
  info.append(make("p", "", "status"), primary, secondary);
  row.append(jacket, info);
  return row;
}

function renderShelf() {
  const shelf = element("shelf");
  const existing = new Map(
    Array.from(shelf.children, (row) => [row.dataset.id, row]),
  );
  state.books.forEach((book, position) => {
    let row = existing.get(book.id);
    existing.delete(book.id);
    if (!row || row.dataset.registration !== String(book.registration_seq)) {
      row?.remove();
      row = shelfBook(book);
    }
    const action = row.querySelector(".book-action");
    let label = "";
    if (book.status === "ready") {
      label =
        localStorage.getItem("diorama:section:" + book.id) === null
          ? "Start Reading"
          : "Continue reading";
    } else if (book.status === "needs_account") label = "Connect ChatGPT";
    else if (book.status === "failed") label = "Retry preparation";
    action.hidden = !label;
    action.textContent = label;
    const status = row.querySelector(".status");
    status.hidden = book.status === "ready";
    status.textContent = labels[book.status] || "";
    const remove = row.querySelector(".shelf-remove");
    remove.disabled = book.status === "preparing";
    remove.textContent = remove.disabled
      ? "Remove from library (after preparation)"
      : "Remove from library";
    remove.setAttribute("aria-label", remove.textContent + " · " + book.title);
    if (shelf.children[position] !== row)
      shelf.insertBefore(row, shelf.children[position] || null);
  });
  for (const row of existing.values()) row.remove();
  element("count").textContent =
    state.books.length + (state.books.length === 1 ? " book" : " books");
  element("hero-add").textContent = state.books.length
    ? "Add an EPUB"
    : "Choose your first EPUB";
}

let shelfRequest = 0;
async function refreshBooks(version) {
  if (document.hidden) return;
  const request = ++shelfRequest;
  const books = await api("books");
  if (
    version !== state.version ||
    request !== shelfRequest ||
    state.view !== "library"
  )
    return;
  message("error");
  state.books = books;
  if (
    followed &&
    !books.some(
      (book) =>
        book.id === followed.id &&
        book.registration_seq === followed.registration_seq,
    )
  )
    follow(null);
  renderShelf();
  if (
    books.some((book) =>
      ["queued", "preparing", "needs_account", "removing"].includes(
        book.status,
      ),
    )
  )
    schedule(version, refreshBooks);
}

element("add").onclick = element("hero-add").onclick = () =>
  element("file").click();
const removeDialog = element("remove-dialog");
function openRemoval(id, trigger) {
  const book = state.books.find((book) => book.id === id);
  if (!book || book.status === "preparing") return;
  removeDialog.dataset.id = book.id;
  element("remove-title").textContent = "Remove “" + book.title + "”?";
  message("remove-error");
  trigger.focus({ preventScroll: true });
  removeDialog.showModal();
}
element("cancel-remove").onclick = () => removeDialog.close();
removeDialog.oncancel = (event) => {
  if (element("confirm-remove").disabled) event.preventDefault();
};
element("remove-form").onsubmit = async (event) => {
  event.preventDefault();
  const confirm = element("confirm-remove");
  if (confirm.disabled) return;
  const id = removeDialog.dataset.id;
  const book = state.books.find((book) => book.id === id);
  confirm.disabled = element("cancel-remove").disabled = true;
  message("remove-error");
  try {
    await api("books/" + encodeURIComponent(id), { method: "DELETE" });
    localStorage.removeItem("diorama:section:" + id);
    if (followed?.id === id) follow(null);
    ++shelfRequest;
    state.books = state.books.filter((book) => book.id !== id);
    removeDialog.close();
    if (state.view === "library") {
      renderShelf();
      message(
        "notice",
        book
          ? "Removed “" + book.title + "” from your library."
          : "Book removed.",
      );
      (
        element("shelf").querySelector(
          ".book-action:not([hidden]), .trace-link",
        ) || element("add")
      ).focus();
    }
  } catch (error) {
    message("remove-error", error.message);
  } finally {
    confirm.disabled = element("cancel-remove").disabled = false;
  }
};
element("file").onchange = async () => {
  const input = element("file");
  const file = input.files[0];
  if (!file || uploading) return;
  uploading = true;
  element("hero-add").disabled = element("add").disabled = true;
  message("error");
  message("notice", "Uploading " + file.name + "…");
  try {
    if (file.size > 128 * 1024 * 1024)
      throw new Error("Choose an EPUB under 128 MiB.");
    const book = await api("books", {
      method: "POST",
      headers: { "Content-Type": "application/epub+zip" },
      body: file,
    });
    ++shelfRequest;
    follow(book);
    message(
      "notice",
      book.status === "ready"
        ? "This edition is already on your shelf."
        : "EPUB received.",
    );
    if (state.view === "library") await refreshBooks(state.version);
  } catch (error) {
    message("notice");
    message("error", error.message);
  } finally {
    input.value = "";
    uploading = false;
    element("hero-add").disabled = element("add").disabled = false;
  }
};

async function accountAction(path) {
  const version = state.version;
  element("account")
    .querySelectorAll("button")
    .forEach((node) => (node.disabled = true));
  message("error");
  try {
    const result = await api("account/" + path, { method: "POST" });
    if (state.version === version) renderAccount(result, version);
  } catch (error) {
    if (state.version === version) {
      message("error", error.message);
      element("account").replaceChildren(
        button("Check connection", () => refreshAccount(version)),
      );
    }
  }
}

function renderAccount(account, version) {
  const area = element("account");
  area.replaceChildren();
  if (account.status === "pending") {
    const link = make("a", "Open ChatGPT sign-in ↗");
    link.href = account.verification_url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    area.append(
      make("p", "Enter this code on the ChatGPT verification page."),
      make("code", account.user_code),
      link,
      button("Cancel", () => accountAction("cancel")),
    );
    schedule(version, refreshAccount, 2200);
  } else if (account.status === "connected") {
    const back = make("a", "Return to library →");
    back.href = "#library";
    area.append(
      make("p", "Connected"),
      back,
      button("Disconnect", () => accountAction("disconnect")),
    );
  } else {
    area.append(
      make("p", account.message || "Disconnected"),
      button("Connect ChatGPT", () => accountAction("connect")),
    );
  }
}

async function refreshAccount(version) {
  const account = await api("account");
  if (state.version === version) {
    message("error");
    const area = element("account");
    if (
      account.status === "pending" &&
      area.querySelector("code")?.textContent === account.user_code
    ) {
      schedule(version, refreshAccount, 2200);
    } else renderAccount(account, version);
  }
}

async function showPage(number, version) {
  const request = ++pageRequest;
  const page = state.pages[number];
  if (!page) return;
  const digest = state.readerId;
  const data = await api("books/" + digest + "/pages/" + number);
  if (request !== pageRequest || version !== state.version) return;
  message("error");
  state.page = number;
  element("section-name").textContent = page.trail.at(-1);
  element("passage").textContent = data.text;
  element("contents").value = String(number);
  element("prev").disabled = number === 0;
  element("next").disabled = number === state.pages.length - 1;
  localStorage.setItem("diorama:section:" + digest, String(number));
  window.scrollTo(0, 0);
}

function turnPage(number) {
  showPage(number, state.version).catch((error) =>
    message("error", error.message),
  );
}
element("prev").onclick = () => turnPage(state.page - 1);
element("next").onclick = () => turnPage(state.page + 1);
element("contents").onchange = (event) => turnPage(Number(event.target.value));

async function navigate() {
  observer.close();
  readerBook = null;
  const version = ++state.version;
  clearTimeout(state.timer);
  const [page, id] = location.hash.slice(1).split("/");
  state.view =
    page === "reader" && id
      ? "reader"
      : page === "settings"
        ? "settings"
        : "library";
  updateFooterTrace();
  for (const name of ["library", "reader", "settings"])
    element(name).hidden = name !== state.view;
  document.querySelectorAll("nav a").forEach((link) => {
    if (link.hash === "#" + state.view)
      link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  message("error");
  document.title =
    "Diorama · " + state.view[0].toUpperCase() + state.view.slice(1);
  window.scrollTo(0, 0);
  try {
    if (state.view === "library") await refreshBooks(version);
    else if (state.view === "settings") {
      element("account").textContent = "Checking connection…";
      await refreshAccount(version);
    } else {
      state.readerId = id;
      state.pages = [];
      element("passage").textContent = "Opening book…";
      element("book-name").textContent = "";
      element("section-name").textContent = "";
      element("contents").replaceChildren();
      element("prev").disabled = true;
      element("next").disabled = true;
      const { book, pages } = await api(
        "books/" + encodeURIComponent(id) + "/contents",
      );
      if (version !== state.version) return;
      readerBook = book;
      updateFooterTrace();
      document.title = book.title + " · Diorama";
      element("book-name").textContent = book.title;
      state.pages = pages;
      element("contents").replaceChildren(
        ...pages.map(
          (p) =>
            new Option(
              p.number +
                1 +
                ". " +
                (p.trail.length > 1 ? p.trail.slice(1) : p.trail).join(" › "),
              String(p.number),
            ),
        ),
      );
      await showPage(Math.min(savedSection(id), pages.length - 1), version);
    }
  } catch (error) {
    if (version !== state.version) return;
    message("error", error.message);
    if (state.view === "settings")
      element("account").replaceChildren(
        button("Check connection", () => refreshAccount(version)),
      );
    if (state.view === "reader") element("passage").textContent = "";
  }
}

document.querySelector(".skip").onclick = (event) => {
  event.preventDefault();
  element("main-content").focus();
};
document.addEventListener("visibilitychange", () => {
  if (state.view === "library" && !document.hidden)
    refreshBooks(state.version).catch((error) =>
      message("error", error.message),
    );
});
window.addEventListener("hashchange", navigate);
navigate();
