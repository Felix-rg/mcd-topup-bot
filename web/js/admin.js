async function loadLoginBranding() {
    try {
        const res = await fetch("/api/site-settings");
        if (!res.ok) return;
        const data = await res.json();
        const title = document.getElementById("loginTitle");
        const logo = document.getElementById("loginLogo");
        const siteName = data.site_name || "LIXAFA";
        if (title) title.innerText = `${siteName} Dashboard`;
        if (logo && data.logo_url) {
            logo.innerHTML = "";
            logo.classList.add("has-image");
            const img = document.createElement("img");
            img.src = data.logo_url;
            img.alt = siteName;
            logo.appendChild(img);
        } else if (logo) {
            logo.classList.remove("has-image");
            logo.innerText = "L";
        }
    } catch (error) {
        console.error("Gagal memuat branding login:", error);
    }
}

async function login() {
    const username = document.getElementById("username").value;
    const password = document.getElementById("password").value;
    const message = document.getElementById("msg");

    const res = await fetch("/admin/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password })
    });

    const data = await res.json();
    if (res.status !== 200) {
        message.innerText = data.detail || "Login gagal";
        return;
    }

    localStorage.setItem("admin_token", data.token);
    localStorage.setItem("admin_user", JSON.stringify({
        username: data.username,
        role: data.role,
        permissions: data.permissions || []
    }));

    window.location.href = "/admin-dashboard";
}

document.addEventListener("DOMContentLoaded", loadLoginBranding);
