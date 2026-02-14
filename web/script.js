let currentOrderId = null;

window.onload = function() {
  const last = localStorage.getItem("last_order_id");
  if (last) {
    currentOrderId = last;

    // langsung ambil status terakhir
    fetch("http://127.0.0.1:8000/topup/" + currentOrderId)
      .then(res => res.json())
      .then(data => {
        document.getElementById("popupStatus").innerText =
          "Status: " + data.status;

        document.getElementById("popupLink").innerHTML =
          "<a href='" + data.invoice_url + "' target='_blank'>Klik di sini untuk bayar</a>";
      });
  }
};



async function buatOrder() {
  const phone = document.getElementById("phone").value;
  const provider = document.getElementById("provider").value;
  const nominal = document.getElementById("nominal").value;

  const response = await fetch("http://127.0.0.1:8000/topup", {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      phone: phone,
      provider: provider,
      nominal: nominal,
      method: "QRIS"
    })
  });

  const data = await response.json();

  currentOrderId = data.id;
    localStorage.setItem("last_order_id", data.id);


    
  document.getElementById("popup").style.display = "block";
  document.getElementById("popupStatus").innerText = "Status: pending";
  document.getElementById("popupLink").innerHTML =
    "<a href='" + data.invoice_url + "' target='_blank'>Klik di sini untuk bayar</a>";

  cekStatus();
}

async function cekStatus() {
  if (!currentOrderId) return;

  const response = await fetch("http://127.0.0.1:8000/topup/" + currentOrderId);
  const data = await response.json();

  document.getElementById("popupStatus").innerText =
    "Status: " + data.status;

  if (data.status.toLowerCase() !== "paid") {
    setTimeout(cekStatus, 5000);
  }
}

function tutupPopup() {
  document.getElementById("popup").style.display = "none";
}

function bukaStatusLagi() {
  if (!currentOrderId) {
    alert("Belum ada transaksi.");
    return;
  }
  document.getElementById("popup").style.display = "block";
}
