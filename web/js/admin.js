async function login(){

const username = document.getElementById("username").value
const password = document.getElementById("password").value

const res = await fetch("/admin/login",{
method:"POST",
headers:{
"Content-Type":"application/json"
},
body:JSON.stringify({
username:username,
password:password
})
})

const data = await res.json()

if(res.status !== 200){
document.getElementById("msg").innerText="Login gagal"
return
}

localStorage.setItem("admin_token",data.token)

window.location.href="/admin-dashboard"

}