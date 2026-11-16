"""Agenda tab for the already authenticated operator console."""
export function installAgenda({ operatorApi, humanError }) {
  const $ = (id) => document.getElementById(id);
  const list = $("agendaList");
  if (!list) return { refresh: async () => {} };
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || "America/Recife";
  $("agendaTimezone").textContent = "Fuso do aparelho: " + zone + ". Datas são confirmadas e preservadas no Core com fuso explícito.";
  let busy = false;
  let pendingRequest = null;

  const notice = (message) => { $("agendaNotice").textContent = message; };
  const span = (text, className) => {
    const node = document.createElement("span");
    node.className = className || "";
    node.textContent = text;
    return node;
  };
  const niceDate = (iso) => {
    if (!iso) return "Sem próxima ocorrência";
    try { return new Intl.DateTimeFormat("pt-BR", {
      timeZone: zone, day: "2-digit", month: "2-digit", year: "numeric",
      hour: "2-digit", minute: "2-digit",
    }).format(new Date(iso)); } catch { return "Data inválida"; }
  };

  async function refresh() {
    const year = $("agendaYear").value.trim();
    if (year && !/^(20|21)\d{2}$/.test(year)) { notice("Informe um ano válido."); return; }
    const results = await operatorApi("/v1/operator/agenda" + (year ? "?year=" + year : ""));
    list.replaceChildren();
    if (!results.items.length) { list.append(span("Nenhuma agenda encontrada.")); return; }
    for (const item of results.items) {
      const card = document.createElement("div");
      card.className = "agenda-entry";
      const title = document.createElement("strong");
      title.textContent = item.text;
      const status = {
        scheduled: "Agendada", cancelled: "Cancelada",
        finished: "Repetição concluída", past_due: "Data passada",
        delivered: "Entregue",
      }[item.status] || item.status;
      const details = span(
        "Início: " + niceDate(item.due_at) + " · " + status +
        (item.interval_minutes ? " · A cada " + item.interval_minutes + " min" : "") +
        (item.until_at ? " · Até " + niceDate(item.until_at) : ""),
        "subtle",
      );
      card.append(title, details);
      if (item.next_due_at) card.append(span("Próximo: " + niceDate(item.next_due_at), "subtle"));
      if (item.status === "scheduled") {
        const cancel = document.createElement("button");
        cancel.type = "button";
        cancel.className = "ghost danger";
        cancel.textContent = "Cancelar agenda";
        cancel.addEventListener("click", async () => {
          if (!confirm("Cancelar esta agenda? Avisos ainda não enviados serão cancelados.")) return;
          cancel.disabled = true;
          try {
            await operatorApi("/v1/operator/agenda/" + encodeURIComponent(item.id), { method: "DELETE" });
            notice("Agenda cancelada.");
            await refresh();
          } catch (error) { notice("Não foi possível cancelar: " + humanError(error)); cancel.disabled = false; }
        });
        card.append(cancel);
      }
      list.append(card);
    }
  }

  $("agendaCreateForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy) return;
    const text = $("agendaText").value.trim();
    const due = $("agendaDue").value;
    const until = $("agendaUntil").value;
    const interval = $("agendaRepeat").value;
    if (!text || !due) { notice("Informe texto e horário."); return; }
    if (interval && !until) { notice("Escolha quando a repetição deve terminar."); return; }
    if (!interval && until) { notice("Para uma tarefa única, deixe o campo Repetir até vazio."); return; }
    const start = new Date(due), stop = until ? new Date(until) : null;
    if (Number.isNaN(start.getTime()) || stop && Number.isNaN(stop.getTime())) {
      notice("Horário inválido."); return;
    }
    if (start.getTime() <= Date.now() + 10_000) { notice("Escolha um horário futuro."); return; }
    const key = JSON.stringify([text, due, until, interval]);
    if (pendingRequest?.key !== key) {
      pendingRequest = { key, id: globalThis.crypto.randomUUID() };
    }
    busy = true;
    $("agendaSave").disabled = true;
    notice("Registrando agenda…");
    try {
      await operatorApi("/v1/operator/agenda", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          request_id: pendingRequest.id, text,
          due_at: start.toISOString(), timezone: zone,
          interval_minutes: interval ? Number(interval) : null,
          until_at: stop ? stop.toISOString() : null,
        }),
      });
      pendingRequest = null;
      $("agendaText").value = "";
      notice("Agenda salva no Core. Você pode conferir na lista abaixo.");
      await refresh();
    } catch (error) {
      notice("Não foi possível registrar: " + humanError(error));
    } finally { busy = false; $("agendaSave").disabled = false; }
  });
  $("agendaRefresh").addEventListener("click", () => refresh().catch((e) => notice(humanError(e))));
  $("agendaYear").addEventListener("change", () => refresh().catch((e) => notice(humanError(e))));
  return { refresh };
}
