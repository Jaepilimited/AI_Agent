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
  var forgotLink = document.getElementById("forgot-link");
  var forgotBox = document.getElementById("forgot-box");
  var forgotNote = document.getElementById("forgot-note");
  var forgotSubmit = document.getElementById("forgot-submit");
  var forgotMsg = document.getElementById("forgot-msg");

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
    if (forgotLink) forgotLink.hidden = signup;
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
    if (forgotBox) forgotBox.hidden = true;
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

  // ── 비밀번호 재설정 관리자 요청 ────────────────────────────────────────────
  // 이 경로는 계정을 직접 바꾸지 않는다. 관리자가 본인 확인 후 임시 비밀번호를 발급한다.
  if (forgotLink && forgotBox) {
    forgotLink.addEventListener("click", function () {
      forgotBox.hidden = !forgotBox.hidden;
      forgotMsg.textContent = "";
      forgotMsg.classList.remove("is-error");
      if (!forgotBox.hidden) forgotNote.focus();
    });
  }

  function forgotSay(text, isError) {
    forgotMsg.textContent = text;
    forgotMsg.classList.toggle("is-error", !!isError);
  }

  if (forgotSubmit) {
    forgotSubmit.addEventListener("click", async function () {
      // ⚠️ 이름만으로는 누구인지 확정되지 않는다 (동명이인 때문에 팀까지 고른다).
      //    여기서 막지 않으면 관리자에게 "누구인지 모를 요청" 이 쌓인다.
      if (!selectedUser) {
        forgotSay("이름을 입력하고 소속 팀까지 선택해 주세요.", true);
        return;
      }
      forgotSubmit.disabled = true;
      forgotSay("요청을 보내는 중…", false);
      try {
        var res = await fetch("/api/auth/password-reset-request", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            department: selectedUser.department,
            name: selectedUser.display_name,
            id: selectedUser.id,
            note: (forgotNote.value || "").trim()
          })
        });
        // ⛔ `data.ok` 만 보면 안 된다 — 서버가 400/500 을 주면 그 필드가 아예 없어
        //    실패가 성공으로 보인다 (2026-08-27 저장 버튼에서 실제로 겪었다).
        if (!res.ok) throw new Error("요청 접수에 실패했습니다.");
        var data = await res.json();
        forgotSay(data.message || "요청이 접수되었습니다.", false);
        forgotNote.value = "";
      } catch (err) {
        forgotSay("요청을 보내지 못했습니다. 잠시 후 다시 시도해 주세요.", true);
      } finally {
        forgotSubmit.disabled = false;
      }
    });
  }

  function escapeHtml(s) {
    var div = document.createElement("div");
    div.textContent = s;
    return div.innerHTML;
  }

  // Initialize
  setMode(false);
})();
