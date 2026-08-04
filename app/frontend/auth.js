/* Cella — auth.js
   Login / Signup: Name → Team → Password (AD-linked)
*/

(function () {
  "use strict";

  var form = document.getElementById("auth-form");
  var nameInput = document.getElementById("input-name");
  var deptSelect = document.getElementById("input-dept");
  var passwordInput = document.getElementById("input-password");
  var submitBtn = document.getElementById("btn-submit");
  var toggleLink = document.getElementById("toggle-link");
  var errorMsg = document.getElementById("error-msg");
  var formTitle = document.getElementById("form-title");

  var isSignup = false;
  var selectedUser = null;
  var matchedUsers = []; // All users matching current name
  var debounceTimer = null;

  // ── Create autocomplete dropdown ──
  var acList = document.createElement("div");
  acList.className = "ac-dropdown";
  acList.style.display = "none";
  nameInput.parentNode.insertBefore(acList, nameInput.nextSibling);

  function setMode(signup) {
    isSignup = signup;
    if (signup) {
      submitBtn.textContent = "회원가입";
      toggleLink.textContent = "이미 계정이 있으신가요? 로그인";
      formTitle.textContent = "회원가입";
    } else {
      submitBtn.textContent = "로그인";
      toggleLink.textContent = "계정이 없으신가요? 회원가입";
      formTitle.textContent = "Welcome Back";
    }
    errorMsg.textContent = "";
  }

  toggleLink.addEventListener("click", function () {
    setMode(!isSignup);
  });

  // ── Extract last segment from department path ──
  function lastTeam(dept) {
    if (!dept) return "";
    var parts = dept.split(" > ");
    return parts[parts.length - 1];
  }

  // ── Update team select based on matched users ──
  function updateTeamSelect(users) {
    matchedUsers = users;
    deptSelect.innerHTML = "";

    if (!users || users.length === 0) {
      var opt = document.createElement("option");
      opt.value = "";
      opt.textContent = "-- 이름을 먼저 입력하세요 --";
      deptSelect.appendChild(opt);
      deptSelect.disabled = true;
      selectedUser = null;
      return;
    }

    if (users.length === 1) {
      // Single match — auto-select
      var opt = document.createElement("option");
      opt.value = users[0].department;
      opt.textContent = lastTeam(users[0].department);
      opt.selected = true;
      deptSelect.appendChild(opt);
      deptSelect.disabled = true;
      selectedUser = users[0];
    } else {
      // Multiple matches — let user pick
      var placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = "-- 팀을 선택하세요 --";
      deptSelect.appendChild(placeholder);

      users.forEach(function (u) {
        var opt = document.createElement("option");
        opt.value = u.department;
        opt.textContent = lastTeam(u.department);
        deptSelect.appendChild(opt);
      });
      deptSelect.disabled = false;
      selectedUser = null;
    }
  }

  // ── Team select change handler ──
  deptSelect.addEventListener("change", function () {
    var dept = deptSelect.value;
    if (!dept) { selectedUser = null; return; }
    for (var i = 0; i < matchedUsers.length; i++) {
      if (matchedUsers[i].department === dept) {
        selectedUser = matchedUsers[i];
        passwordInput.focus();
        return;
      }
    }
    selectedUser = null;
  });

  // ── Select a user from autocomplete ──
  function selectUser(user) {
    nameInput.value = user.display_name;
    acList.style.display = "none";
    errorMsg.textContent = "";

    // Find all users with same display_name
    var sameNameUsers = matchedUsers.filter(function (u) {
      return u.display_name === user.display_name;
    });

    if (sameNameUsers.length > 1) {
      updateTeamSelect(sameNameUsers);
    } else {
      updateTeamSelect([user]);
    }

    if (selectedUser) {
      passwordInput.focus();
    } else {
      deptSelect.focus();
    }
  }

  // ── Search for name when user types ──
  nameInput.addEventListener("input", function () {
    clearTimeout(debounceTimer);
    selectedUser = null;
    var name = nameInput.value.trim();

    if (name.length < 2) {
      acList.style.display = "none";
      updateTeamSelect([]);
      return;
    }

    debounceTimer = setTimeout(async function () {  // 150ms debounce
      try {
        var resp = await fetch("/api/auth/search-name?name=" + encodeURIComponent(name));
        if (!resp.ok) return;
        var users = await resp.json();
        matchedUsers = users;

        if (users.length === 0) {
          acList.style.display = "none";
          updateTeamSelect([]);
          return;
        }

        // Build autocomplete dropdown
        acList.innerHTML = "";
        users.forEach(function (u) {
          var item = document.createElement("div");
          item.className = "ac-item";
          item.innerHTML = '<span class="ac-name">' + escapeHtml(u.display_name) + '</span>'
            + '<span class="ac-team">' + escapeHtml(lastTeam(u.department)) + '</span>';
          item.addEventListener("click", function () {
            selectUser(u);
          });
          acList.appendChild(item);
        });
        acList.style.display = "block";
      } catch (e) {
        console.error("Name search failed:", e);
      }
    }, 150);
  });

  // Close autocomplete when clicking outside
  document.addEventListener("click", function (e) {
    if (!nameInput.contains(e.target) && !acList.contains(e.target)) {
      acList.style.display = "none";
    }
  });

  // ── Form submit ──
  form.addEventListener("submit", async function (e) {
    e.preventDefault();
    errorMsg.textContent = "";
    submitBtn.disabled = true;

    var name = nameInput.value.trim();
    var password = passwordInput.value;

    if (!name || name.length < 2) {
      errorMsg.textContent = "이름을 입력해 주세요";
      submitBtn.disabled = false;
      return;
    }

    if (!password || password.length < 4) {
      errorMsg.textContent = "비밀번호는 4자 이상이어야 합니다";
      submitBtn.disabled = false;
      return;
    }

    if (!selectedUser) {
      errorMsg.textContent = "이름과 소속 팀을 선택해 주세요";
      submitBtn.disabled = false;
      return;
    }

    var url = isSignup ? "/api/auth/signup" : "/api/auth/signin";
    var body = {
      department: selectedUser.department,
      name: selectedUser.display_name,
      password: password,
      id: selectedUser.id || null,
    };

    try {
      var resp = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });

      if (resp.ok) {
        window.location.href = "/";
      } else {
        var data = await resp.json().catch(function () { return {}; });
        errorMsg.textContent = data.detail || "인증에 실패했습니다";
      }
    } catch (err) {
      errorMsg.textContent = "네트워크 오류. 다시 시도해 주세요.";
    }

    submitBtn.disabled = false;
  });

  function escapeHtml(s) {
    var div = document.createElement("div");
    div.textContent = s;
    return div.innerHTML;
  }

  // Initialize
  setMode(false);
})();
