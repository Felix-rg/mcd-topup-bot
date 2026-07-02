async function login() {
    const username = document.getElementById("username").value.trim();
    const password = document.getElementById("password").value;
    const msg = document.getElementById("msg");
    const button = document.querySelector(".login-card button");

    if (!username || !password) {
        msg.innerText = "Username dan password wajib diisi.";
        return;
    }

    const originalText = button.innerHTML;
    button.disabled = true;
    button.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Memproses...';
    msg.innerText = "";

    try {
        const response = await fetch("/admin/login", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify({ username, password }),
        });

        const data = await response.json();

        if (!response.ok) {
            msg.innerText = data.detail || "Login gagal";
            return;
        }

        localStorage.setItem("admin_token", data.token || data.access_token);
        window.location.href = "/admin-dashboard";
    } catch (error) {
        console.error(error);
        msg.innerText = "Server tidak merespons. Coba lagi.";
    } finally {
        button.disabled = false;
        button.innerHTML = originalText;
    }
}