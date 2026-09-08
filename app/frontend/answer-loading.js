(function (global) {
  "use strict";

  var LABELS = {
    direct: "생각하는 중",
    bigquery: "데이터 확인 중",
    notion: "관련 문서 찾는 중",
    cs: "제품 정보 확인 중",
    gws: "Google 자료 확인 중",
    multi: "여러 자료 종합 중"
  };

  function _indicator(target) {
    if (!target) return null;
    if (target.classList && target.classList.contains("typing-indicator")) return target;
    return target.querySelector(".typing-indicator");
  }

  function _elapsedSeconds(indicator) {
    var startedAt = Number(indicator.dataset.answerStartedAt || Date.now());
    return Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
  }

  function _updateElapsed(indicator) {
    var elapsed = indicator.querySelector(".answer-loading-elapsed");
    if (elapsed) elapsed.textContent = _elapsedSeconds(indicator) + "초 경과";
  }

  function _startClock(indicator) {
    if (indicator.dataset.answerTimerId) return;
    var timerId = global.setInterval(function () {
      if (!indicator.isConnected) {
        global.clearInterval(timerId);
        delete indicator.dataset.answerTimerId;
        return;
      }
      _updateElapsed(indicator);
    }, 250);
    indicator.dataset.answerTimerId = String(timerId);
  }

  function render(target, route) {
    var indicator = _indicator(target);
    if (!indicator) return null;

    if (!indicator.dataset.answerStartedAt) {
      indicator.dataset.answerStartedAt = String(Date.now());
    }

    var label = LABELS[route] || LABELS.direct;

    delete indicator.dataset.answerProgress;
    indicator.dataset.answerRoute = route || "direct";
    indicator.className = "typing-indicator answer-loading";
    indicator.setAttribute("role", "status");
    indicator.setAttribute("aria-live", "polite");
    indicator.setAttribute("aria-label", label);
    indicator.innerHTML =
      '<span class="answer-loading-mark" aria-hidden="true"></span>' +
      '<span class="answer-loading-body">' +
        '<span class="answer-loading-head">' +
          '<span class="answer-loading-label">' + label + '</span>' +
        '</span>' +
        '<span class="answer-loading-track" role="progressbar" aria-label="답변 생성 중" ' +
          'aria-valuetext="답변 생성 중">' +
          '<span class="answer-loading-fill"></span>' +
        '</span>' +
        '<span class="answer-loading-elapsed"></span>' +
      '</span>';

    _updateElapsed(indicator);
    _startClock(indicator);
    return indicator;
  }

  function destroy(target) {
    var indicator = _indicator(target);
    if (!indicator) return;
    var timerId = Number(indicator.dataset.answerTimerId || 0);
    if (timerId) global.clearInterval(timerId);
    delete indicator.dataset.answerTimerId;
  }

  global.CellaAnswerLoading = {
    render: render,
    destroy: destroy
  };
})(window);
