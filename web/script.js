let currentOrderId = null;

function safePaymentUrl(value) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  try {
    const parsed = new URL(raw, window.location.origin);
    if (["http:", "https:"].includes(parsed.protocol)) return parsed.href;
  } catch (_) {
    return "";
  }
  return "";
}

function setPopupStatus(status) {
  const statusEl = document.getElementById("popupStatus");
  if (statusEl) statusEl.textContent = "Status: " + String(status || "-");
}

function setPaymentLink(invoiceUrl) {
  const linkWrap = document.getElementById("popupLink");
  if (!linkWrap) return;

  linkWrap.innerHTML = "";
  const safeUrl = safePaymentUrl(invoiceUrl);
  if (!safeUrl) {
    linkWrap.textContent = "Link pembayaran belum tersedia.";
    return;
  }

  const link = document.createElement("a");
  link.href = safeUrl;
  link.target = "_blank";
  link.rel = "noopener";
  link.textContent = "Klik di sini untuk bayar";
  linkWrap.appendChild(link);
}

window.onload = function () {
  const last = localStorage.getItem("last_order_id");
  if (!last) return;

  currentOrderId = last;
  fetch("/topup/" + encodeURIComponent(currentOrderId))
    .then((res) => (res.ok ? res.json() : Promise.reject(new Error("Order tidak ditemukan"))))
    .then((data) => {
      setPopupStatus(data.status);
      setPaymentLink(data.invoice_url);
    })
    .catch(() => {
      setPopupStatus("Gagal memuat status");
      setPaymentLink("");
    });
};

async function buatOrder() {
  const phone = document.getElementById("phone")?.value || "";
  const provider = document.getElementById("provider")?.value || "";
  const nominal = document.getElementById("nominal")?.value || "";

  const response = await fetch("/topup", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      phone,
      provider,
      nominal,
      method: "QRIS",
    }),
  });

  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.id) {
    alert(data.detail || "Gagal membuat transaksi.");
    return;
  }

  currentOrderId = data.id;
  localStorage.setItem("last_order_id", data.id);

  const popup = document.getElementById("popup");
  if (popup) popup.style.display = "block";
  setPopupStatus("pending");
  setPaymentLink(data.invoice_url);
  cekStatus();
}

async function cekStatus() {
  if (!currentOrderId) return;

  try {
    const response = await fetch("/topup/" + encodeURIComponent(currentOrderId));
    const data = await response.json();
    setPopupStatus(data.status);

    if (String(data.status || "").toLowerCase() !== "paid") {
      setTimeout(cekStatus, 5000);
    }
  } catch (_) {
    setPopupStatus("Gagal memuat status");
  }
}

function tutupPopup() {
  const popup = document.getElementById("popup");
  if (popup) popup.style.display = "none";
}

function bukaStatusLagi() {
  if (!currentOrderId) {
    alert("Belum ada transaksi.");
    return;
  }
  const popup = document.getElementById("popup");
  if (popup) popup.style.display = "block";
}
