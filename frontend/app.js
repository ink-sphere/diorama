const element = (id) => document.getElementById(id);
const state = {
  books: [],
  selected: "",
  view: "",
  version: 0,
  timer: null,
  pages: [],
  page: 0,
};
const labels = {
  queued: "Queued",
  preparing: "Geppetto is preparing this edition",
  needs_account: "ChatGPT connection needed",
  failed: "Preparation failed",
  ready: "Ready to read",
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
    throw new Error(
      typeof error.detail === "string"
        ? error.detail
        : "Diorama could not complete the request.",
    );
  }
  return response.json();
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

function selectBook(id, animate = false) {
  state.selected = id;
  const book = state.books.find((item) => item.id === id);
  const art = element("art");
  const action = element("action");
  for (const entry of element("shelf").children)
    entry.setAttribute("aria-pressed", String(entry.dataset.id === id));
  element("title").textContent = book?.title || "Your library";
  element("author").textContent = book?.authors.join(", ") || "";
  element("excerpt").textContent = book
    ? book.status === "ready"
      ? book.excerpt?.trim() || ""
      : book.message || labels[book.status]
    : "Add an EPUB and Geppetto will prepare it for reading.";
  const source = book?.has_cover
    ? "/api/books/" + id + "/cover"
    : "/assets/seascape.webp";
  if (art.getAttribute("src") !== source) art.src = source;
  action.hidden = false;
  action.disabled = false;
  if (!book) {
    action.textContent = "Choose your first EPUB";
    action.disabled = uploading;
    action.onclick = () => element("file").click();
  } else if (book.status === "ready") {
    action.textContent =
      localStorage.getItem("diorama:section:" + id) === null
        ? "Start reading"
        : "Continue reading";
    action.onclick = () => {
      location.hash = "reader/" + id;
    };
  } else if (book.status === "needs_account") {
    action.textContent = "Connect ChatGPT";
    action.onclick = () => {
      location.hash = "settings";
    };
  } else if (book.status === "failed") {
    action.textContent = "Retry preparation";
    action.onclick = async () => {
      action.disabled = true;
      try {
        await api("books/" + id + "/retry", { method: "POST" });
        await refreshBooks(state.version);
      } catch (error) {
        message("error", error.message);
        action.disabled = false;
      }
    };
  } else {
    action.hidden = true;
  }
  if (animate && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
    element("scene-copy").animate(
      [
        { opacity: 0.65, transform: "translateY(4px)" },
        { opacity: 1, transform: "none" },
      ],
      { duration: 170, easing: "ease-out" },
    );
  }
}

function renderShelf() {
  const shelf = element("shelf");
  const focused = document.activeElement?.dataset.id;
  const entries = state.books.map((book) => {
    const entry = make("button", "", "entry");
    entry.dataset.id = book.id;
    entry.onclick = (event) => selectBook(book.id, event.detail > 0);
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
    const details = make("span", "");
    details.append(
      make("span", book.title, "book-title"),
      make(
        "span",
        book.status === "ready"
          ? book.authors.join(", ") || labels.ready
          : labels[book.status],
        "status",
      ),
    );
    entry.append(jacket, details);
    return entry;
  });
  shelf.replaceChildren(...entries);
  if (focused)
    entries
      .find((entry) => entry.dataset.id === focused)
      ?.focus({ preventScroll: true });
  element("count").textContent =
    state.books.length + (state.books.length === 1 ? " book" : " books");
  selectBook(
    state.books.some((b) => b.id === state.selected)
      ? state.selected
      : state.books[0]?.id || "",
  );
}

let shelfRequest = 0;
async function refreshBooks(version) {
  const request = ++shelfRequest;
  const books = await api("books");
  if (
    version !== state.version ||
    request !== shelfRequest ||
    state.view !== "library"
  )
    return;
  message("error");
  if (
    JSON.stringify(books) !== JSON.stringify(state.books) ||
    !element("shelf").childElementCount
  ) {
    state.books = books;
    renderShelf();
  } else selectBook(state.selected);
  if (
    books.some((book) =>
      ["queued", "preparing", "needs_account"].includes(book.status),
    )
  )
    schedule(version, refreshBooks);
}

element("add").onclick = () => element("file").click();
element("file").onchange = async () => {
  const input = element("file");
  const file = input.files[0];
  if (!file || uploading) return;
  uploading = true;
  element("add").disabled = true;
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
    state.selected = book.id;
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
    element("add").disabled = false;
    if (state.view === "library") selectBook(state.selected);
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
  const digest = state.selected;
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
  const version = ++state.version;
  clearTimeout(state.timer);
  const [page, id] = location.hash.slice(1).split("/");
  state.view =
    page === "reader" && id
      ? "reader"
      : page === "settings"
        ? "settings"
        : "library";
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
      state.selected = id;
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
window.addEventListener("hashchange", navigate);
navigate();
