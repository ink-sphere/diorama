export function observeIngestion({ api, element, make, onMissing }) {
  const sheet = element("trace-sheet");
  const list = element("trace-list");
  const scroll = element("trace-scroll");
  const preview = element("book-preview");
  const sections = element("preview-section");
  let previewRevision;
  let inspected = null;
  let opener = null;
  let cursor = 0;
  let followTail = false;
  let epoch = 0;
  let timer;
  let backoff = 750;
  let controller;
  let drawerVersion = 0;

  function showPreview(data) {
    if (previewRevision === data.preparation_revision) return;
    previewRevision = data.preparation_revision;
    const entries = [...data.entries];
    if (
      data.opening?.source &&
      !entries.some((entry) => entry.key === data.opening.key)
    )
      entries.unshift(data.opening);
    const selected = sections.value;
    sections.replaceChildren(
      ...entries.map((entry) => {
        const option = new Option(
          "　".repeat(Math.min(entry.depth, 6)) + entry.title,
          entry.key,
        );
        option.disabled = !entry.source;
        return option;
      }),
    );
    const current =
      entries.find((entry) => entry.key === selected && entry.source) ||
      entries.find((entry) => entry.source);
    preview.hidden = !current;
    if (!current) return;
    sections.value = current.key;
    sections.onchange = () => {
      element("source-excerpt").textContent =
        entries.find((entry) => entry.key === sections.value)?.text || "";
    };
    sections.onchange();
  }

  function resetRows() {
    ++drawerVersion;
    cursor = 0;
    previewRevision = undefined;
    preview.hidden = true;
    preview.open = false;
    sections.replaceChildren();
    element("source-excerpt").textContent = "";
    followTail = false;
    list.replaceChildren();
    element("trace-count").textContent = "";
    element("trace-status").textContent = "Loading history…";
    scroll.scrollTop = 0;
  }

  function open(book, from) {
    if (!book) return;
    opener = from;
    inspected = book;
    resetRows();
    element("trace-book").textContent = book.title || "";
    element("retry-trace").hidden = true;
    sheet.showModal();
    refresh();
  }

  function describe(row) {
    if (row.origin === "library") return row.note;
    if (row.name === "capture_gap")
      return "Some execution records could not be saved.";
    if (row.name === "turn/completed") return "Turn " + row.status;
    const action = row.item_type === "fileChange" ? "File change" : "Command";
    return (
      action +
      (row.name === "item/started" ? " started" : " finished") +
      (row.exit_code == null ? "" : " · exit " + row.exit_code)
    );
  }

  function append(row, book, arriving) {
    const li = make("li", "", arriving ? "arriving" : "");
    const details = make("details", "");
    const summary = make("summary", "");
    const source =
      row.name === "capture_gap"
        ? "Trace recorder"
        : row.origin === "sdk"
          ? "Geppetto SDK"
          : "Library";
    const timestamp =
      row.observed_at_ms == null ? null : new Date(row.observed_at_ms);
    let stamp = timestamp ? timestamp.toLocaleTimeString() : "Time unrecorded";
    if (timestamp && book.registered_at_ms != null) {
      const seconds = (row.observed_at_ms - book.registered_at_ms) / 1000;
      stamp = "+" + seconds.toFixed(1) + "s";
    }
    const meta = make("span", source + " · " + stamp, "trace-stamp");
    if (timestamp) meta.title = timestamp.toLocaleString();
    summary.append(meta, make("span", describe(row), "trace-action"));
    const content = make("div", "", "trace-fields");
    details.append(summary, content);
    li.append(details);
    list.append(li);
    const version = drawerVersion;
    let loaded = false;
    details.ontoggle = async () => {
      if (!details.open || loaded || version !== drawerVersion) return;
      followTail = false;
      loaded = true;
      content.textContent = "Loading fields…";
      try {
        const record = await api("books/" + book.id + "/trace/" + row.seq);
        if (version !== drawerVersion || !li.isConnected) return;
        content.replaceChildren();
        const fields = {
          command: record.fields?.command,
          aggregatedOutput: record.fields?.aggregatedOutput,
          ...record.fields,
          method: record.name,
          run: record.run,
          attempt: record.attempt,
          purpose: record.purpose,
          threadId: record.thread_id,
          turnId: record.turn_id,
          itemId: record.item_id,
        };
        if (record.origin === "library") {
          fields.observation = record.note;
          if (record.outline) fields.outline = record.outline;
        }
        for (const [key, value] of Object.entries(fields)) {
          if (value == null) continue;
          content.append(
            make("b", key),
            make(
              "pre",
              typeof value === "object"
                ? JSON.stringify(value, null, 2)
                : String(value),
            ),
          );
        }
        for (const [key, marker] of Object.entries(record.markers || {})) {
          content.append(
            make(
              "p",
              key +
                (marker.omitted
                  ? " · omitted for privacy"
                  : " · shortened from " +
                    (marker.original_bytes != null
                      ? marker.original_bytes + " bytes"
                      : marker.original_count + " entries")),
            ),
          );
        }
      } catch {
        if (version !== drawerVersion) return;
        content.textContent =
          "Couldn’t load fields. Close and reopen this entry to retry.";
        loaded = false;
      }
    };
  }

  async function poll(version, signal) {
    if (document.hidden || !sheet.open || !inspected) return;
    const book = inspected;
    const current = () => version === epoch && !signal.aborted && sheet.open;
    function unavailable() {
      resetRows();
      element("trace-status").textContent =
        "This edition is no longer on your shelf.";
      element("retry-trace").hidden = true;
      onMissing(book);
    }
    try {
      const data = await api("books/" + book.id + "/activity", { signal });
      if (!current()) return;
      if (
        book.registration_seq != null &&
        book.registration_seq !== data.registration_seq
      ) {
        unavailable();
        return;
      }
      element("trace-book").textContent = data.title;
      showPreview(data);
      element("retry-trace").hidden = true;
      const arriving = cursor > 0;
      while (cursor < data.trace_revision) {
        const page = await api("books/" + book.id + "/trace?after=" + cursor, {
          signal,
        });
        if (!current()) return;
        if (page.registration_seq !== data.registration_seq) {
          unavailable();
          return;
        }
        for (const row of page.events) append(row, data, arriving);
        if (followTail) scroll.scrollTop = scroll.scrollHeight;
        cursor = page.next_after;
        if (!page.has_more) break;
      }
      const running = ["queued", "preparing"].includes(data.status);
      element("trace-status").textContent =
        !data.sdk_count && !running
          ? "Execution trace was not recorded for this ingestion."
          : "";
      element("trace-count").textContent = list.children.length + " records";
      backoff = 750;
      if (running) timer = setTimeout(refresh, 750);
    } catch (err) {
      if (!current()) return;
      if (err.status === 404) {
        unavailable();
        return;
      }
      element("trace-status").textContent =
        "Connection interrupted. Reconnecting…";
      element("retry-trace").hidden = false;
      backoff = Math.min(backoff * 2, 12000);
      timer = setTimeout(refresh, backoff);
    }
  }

  function refresh() {
    clearTimeout(timer);
    controller?.abort();
    ++epoch;
    if (!sheet.open || document.hidden) return;
    controller = new AbortController();
    poll(epoch, controller.signal);
  }

  sheet.onclose = () => {
    inspected = null;
    resetRows();
    if (opener?.isConnected && opener.offsetParent)
      opener.focus({ preventScroll: true });
    refresh();
  };
  element("close-trace").onclick = () => sheet.close();
  element("retry-trace").onclick = refresh;
  scroll.addEventListener("scroll", () => {
    if (scroll.scrollHeight - scroll.clientHeight - scroll.scrollTop > 8)
      followTail = false;
  });
  element("trace-latest").onclick = () => {
    followTail = true;
    scroll.scrollTop = scroll.scrollHeight;
  };
  document.addEventListener("visibilitychange", refresh);
  window.addEventListener("online", refresh);
  return {
    open,
    close: () => {
      if (sheet.open) sheet.close();
    },
  };
}
