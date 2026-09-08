/* Cella is a static guide. It observes chat state but never animates or changes expression. */
(function () {
  'use strict';

  var STORAGE_KEY = 'skin1004-cella-guide-v1';
  var main, guide, guideTitle, guideText, hideButton, summonButton, input, messages;
  var observer = null;
  var wasStreaming = false;
  var activeGuideControl = null;
  var contextGuide = {
    key: 'initial',
    title: '프롬프트 가이드',
    text: '원하는 결과를 한 문장으로 적어보세요. 특정 데이터만 보려면 @@를 입력하세요.'
  };
  var state = loadState();
  var guideCopyCursor = Object.create(null);
  var sessionSeed = Math.floor(Date.now() / 60000);

  var GUIDE_COPY = {
    idle: [
      '기간·대상·결과 형식을 함께 적으면 더 정확한 답을 받을 수 있어요.',
      '예: 2026년 7월 Qoo10 매출을 국가별 표로 보여줘.',
      '비교 질문에는 기간과 비교 기준을 함께 넣어보세요.',
      '특정 데이터만 확인하려면 입력창에 @@를 입력해 소스를 고르세요.',
      '숫자가 필요하면 기간, 범위, 원하는 표 모양까지 한 문장에 적어보세요.'
    ],
    choosingSource: [
      '@@ 목록에서 소스를 고르세요. 선택한 소스만 이번 질문에서 조회합니다.',
      '조회할 데이터가 정해졌다면 여기서 소스를 선택하세요. 다른 데이터는 함께 보지 않아요.',
      '@@ 뒤에서 원하는 소스를 선택하면 이번 질문의 데이터 범위가 그 소스로 제한돼요.'
    ],
    shortPrompt: [
      '조금 더 구체적으로 적어주세요. 기간·대상·원하는 결과를 함께 쓰면 좋아요.',
      '무엇을, 언제부터 언제까지, 어떤 형태로 볼지 한 문장에 담아보세요.',
      '질문을 조금만 더 늘려보세요. 대상과 기간이 들어가면 훨씬 정확해져요.'
    ],
    missingPeriod: [
      '조회 기간을 넣어보세요. “2026년 7월”처럼 범위를 분명히 하면 정확해져요.',
      '언제의 데이터인지 알려주세요. 월·분기·시작일과 종료일 중 하나면 충분해요.',
      '기간이 빠졌어요. “지난달” 또는 “2026-07-01부터 07-31까지”처럼 적어보세요.',
      '같은 질문도 기간에 따라 답이 달라져요. 확인할 시점을 추가해 주세요.'
    ],
    missingScope: [
      '비교 기준을 추가해 보세요. 국가별·채널별·제품별 중 원하는 기준을 적으면 좋아요.',
      '어떤 단위로 나눠볼까요? 브랜드, 국가, 플랫폼, 제품 중 하나를 골라 적어보세요.',
      '조회 범위를 더 선명하게 해보세요. 예: “일본 채널별” 또는 “제품별”.',
      '대상을 나눌 기준이 있으면 분석이 쉬워져요. 팀별·브랜드별·SKU별도 가능해요.'
    ],
    missingFormat: [
      '원하는 결과 형식을 덧붙여 보세요. 표·차트·요약 중 하나를 지정할 수 있어요.',
      '답을 어떻게 볼지도 적어주세요. 표, 순위, 추이 차트, 세 줄 요약처럼요.',
      '결과 모양을 정해보세요. “Top 10 표로” 또는 “월별 그래프로”처럼 쓰면 좋아요.',
      '분석 결과를 바로 쓰려면 출력 형식까지 요청해 보세요. 예: 표와 핵심 요약.'
    ],
    ready: [
      '질문이 구체적이에요. 그대로 전송해도 좋아요.',
      '기간·대상·형식이 잘 들어갔어요. 이제 보내면 돼요.',
      '좋은 질문이에요. 원하는 결과가 분명해서 바로 조회할 수 있어요.',
      '조건이 충분해요. 전송한 뒤 필요하면 다음 질문으로 범위를 더 좁혀보세요.',
      '이 정도면 셀라가 이해하기 좋아요. 그대로 물어보세요.'
    ],
    error: [
      '같은 질문을 다시 보내거나, 조건을 줄여서 전송해 보세요.',
      '조건을 한두 개 줄여 다시 시도해 보세요. 기간과 대상은 남겨두는 게 좋아요.',
      '질문을 짧게 나눠보세요. 먼저 전체를 조회하고 다음 질문에서 범위를 좁힐 수 있어요.'
    ],
    streaming: [
      '답변을 만드는 중이에요. 부족한 조건은 다음 질문으로 이어서 요청할 수 있어요.',
      '데이터를 확인하고 있어요. 결과가 나오면 기간과 단위가 맞는지 먼저 살펴보세요.',
      '답변을 준비하고 있어요. 표가 필요하면 다음 질문에서 형식을 바꿔달라고 해도 돼요.',
      '조금만 기다려 주세요. 결과가 넓으면 이어서 국가·채널·제품 기준으로 좁힐 수 있어요.'
    ],
    afterAnswer: [
      '결과가 넓다면 “국가별로 다시 보여줘”처럼 조건을 이어서 좁혀보세요.',
      '이전 답변을 이어서 “표로 바꿔줘” 또는 “상위 10개만 보여줘”라고 해도 돼요.',
      '비교가 더 필요하면 “전월 대비 증감률도 추가해줘”처럼 이어서 요청해 보세요.',
      '원하는 숫자가 없다면 기간이나 조회 단위를 바꿔 바로 다음 질문을 해보세요.',
      '답변에서 궁금한 항목을 그대로 지목하면 같은 맥락으로 더 자세히 볼 수 있어요.'
    ]
  };

  var CONTROL_GUIDES = {
    'sidebar-home-link': ['홈 안내', '현재 대화를 벗어나 새 대화를 시작할 수 있는 홈 화면으로 이동합니다.'],
    'btn-new-chat': ['새 대화', '기존 대화는 목록에 유지하고, 빈 대화를 새로 시작합니다.'],
    'btn-collapse-sidebar': ['사이드바 접기', '대화 목록을 접어 채팅 영역을 더 넓게 사용합니다.'],
    'btn-expand-sidebar': ['사이드바 열기', '접어둔 대화 목록과 메뉴를 다시 엽니다.'],
    'btn-menu': ['메뉴 열기', '작은 화면에서 대화 목록과 메뉴를 엽니다.'],
    'convo-search': ['대화 검색', '저장된 대화의 제목을 입력한 단어로 찾습니다.'],
    'btn-dashboard': ['대시보드', '주요 업무 데이터와 지표를 대시보드에서 확인합니다.'],
    'btn-system-status': ['시스템 상태', '사용 가능한 데이터 소스와 연결 상태를 확인합니다.'],
    'btn-wiki': ['Knowledge Wiki', '프로젝트 지식 지도와 최근 변경 내용을 확인합니다.'],
    'btn-admin': ['관리자 메뉴', '사용자·그룹·권한 같은 관리자 설정을 엽니다.'],
    'btn-briefing-settings': ['설정', '저장한 보고, 잔디 수신, 비밀번호를 한곳에서 관리합니다.'],
    'btn-logout': ['로그아웃', '현재 계정의 접속을 종료하고 로그인 화면으로 이동합니다.'],
    'btn-copy-all': ['대화 전체 복사', '현재 대화의 질문과 답변 전체를 클립보드에 복사합니다.'],
    'btn-gws-connect': ['Google 연결', 'Google 계정을 연결해 권한이 있는 메일·드라이브 데이터를 조회할 수 있게 합니다.'],
    'btn-scroll-bottom': ['최신 답변으로 이동', '긴 대화의 가장 아래에 있는 최신 메시지로 이동합니다.'],
    'btn-attach': ['이미지 첨부', '분석하거나 질문할 이미지 파일을 이번 메시지에 추가합니다.'],
    'btn-send': ['질문 보내기', '작성한 질문과 선택한 데이터 소스를 셀라에게 전송합니다.'],
    'source-filter-badge': ['조회 범위', '현재 질문에 적용되는 데이터 소스 범위를 보여줍니다.'],
    'drawer-close': ['대시보드 닫기', '열려 있는 대시보드를 닫고 채팅으로 돌아갑니다.'],
    'status-drawer-close': ['상태 창 닫기', '시스템 상태 창을 닫고 채팅으로 돌아갑니다.'],
    'wiki-drawer-close': ['Wiki 닫기', 'Knowledge Wiki를 닫고 채팅으로 돌아갑니다.'],
    'admin-drawer-close': ['관리자 메뉴 닫기', '관리자 메뉴를 닫고 채팅으로 돌아갑니다.']
  };

  function loadState() {
    try {
      var saved = JSON.parse(localStorage.getItem(STORAGE_KEY));
      if (saved && typeof saved.visible === 'boolean') return { visible: saved.visible };
    } catch (_) { /* Local storage is optional. */ }
    return { visible: true };
  }

  function saveState() {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); } catch (_) { /* ignore */ }
  }

  function create() {
    main = document.querySelector('.chat-main');
    input = document.getElementById('chat-input');
    messages = document.getElementById('chat-messages');
    if (!main) return;

    var layer = document.createElement('div');
    layer.id = 'cella-pet-layer';
    layer.innerHTML =
      '<aside class="cella-guide" id="cella-guide" aria-label="셀라 화면 가이드">' +
        '<div class="cella-guide-card" role="status" aria-live="polite">' +
          '<strong>프롬프트 가이드</strong>' +
          '<p class="cella-guide-text">원하는 결과를 한 문장으로 적어보세요. 특정 데이터만 보려면 @@를 입력하세요.</p>' +
          '<button class="cella-hide-button" type="button" data-cella-action="hide">숨기기</button>' +
        '</div>' +
        '<div class="cella-static-avatar" aria-hidden="true">' +
          '<img src="/static/cella-avatar.png" alt="">' +
          '<span class="cella-static-shadow"></span>' +
        '</div>' +
      '</aside>' +
      '<button class="cella-summon" type="button" aria-label="셀라 가이드 다시 보기">셀라 보기</button>';
    main.appendChild(layer);

    guide = layer.querySelector('.cella-guide');
    guideTitle = layer.querySelector('.cella-guide-card strong');
    guideText = layer.querySelector('.cella-guide-text');
    hideButton = layer.querySelector('.cella-hide-button');
    summonButton = layer.querySelector('.cella-summon');
    bindEvents();
    render();
    place();
    guidePrompt(input ? input.value : '');
    watchInterface();
  }

  function place() {
    if (!guide || !main) return;
    var mainRect = main.getBoundingClientRect();
    var inputArea = document.getElementById('chat-input-area');
    var inputRect = inputArea && inputArea.getBoundingClientRect();
    var baseBottom = window.innerWidth <= 768 ? 10 : 14;
    var clearance = inputRect ? mainRect.bottom - inputRect.top + 10 : baseBottom;
    guide.style.setProperty('--cella-bottom', Math.max(baseBottom, clearance) + 'px');
    summonButton.style.bottom = Math.max(baseBottom, inputRect ? window.innerHeight - inputRect.top + 12 : baseBottom) + 'px';
  }

  function render() {
    if (!guide) return;
    guide.hidden = !state.visible;
    summonButton.hidden = state.visible;
  }

  function renderGuideCopy(title, text) {
    if (guideTitle && title && guideTitle.textContent !== title) guideTitle.textContent = title;
    if (guideText && text && guideText.textContent !== text) guideText.textContent = text;
  }

  function nextGuideCopy(key, copies) {
    if (contextGuide.key === key && contextGuide.text) return contextGuide.text;
    var cursor = guideCopyCursor[key];
    if (typeof cursor !== 'number') cursor = sessionSeed % copies.length;
    else cursor = (cursor + 1) % copies.length;
    guideCopyCursor[key] = cursor;
    return copies[cursor];
  }

  function setGuide(title, text, key) {
    contextGuide = { key: key || title + ':' + text, title: title, text: text };
    if (!activeGuideControl) renderGuideCopy(title, text);
  }

  function setGuideFromPool(key, title, copies) {
    setGuide(title, nextGuideCopy(key, copies), key);
  }

  function showControlGuide(control, copy) {
    activeGuideControl = control;
    renderGuideCopy(copy[0], copy[1]);
  }

  function clearControlGuide(control) {
    if (control && activeGuideControl !== control) return;
    activeGuideControl = null;
    renderGuideCopy(contextGuide.title, contextGuide.text);
  }

  function trimmedControlText(control) {
    var text = (control.textContent || '').replace(/\s+/g, ' ').trim();
    if (text.length > 34) text = text.substring(0, 34) + '…';
    return text;
  }

  function controlGuide(control) {
    if (!control || control.id === 'chat-input') return null;
    if (control.id && CONTROL_GUIDES[control.id]) return CONTROL_GUIDES[control.id];

    if (control.matches('.cella-hide-button')) {
      return ['셀라 숨기기', '셀라 가이드를 화면에서 숨깁니다. 숨긴 뒤에는 “셀라 보기”로 다시 열 수 있어요.'];
    }
    if (control.matches('.cella-summon')) {
      return ['셀라 보기', '숨겨둔 셀라 가이드를 오른쪽 아래에 다시 표시합니다.'];
    }
    if (control.matches('.suggestion-chip')) {
      return ['추천 질문', '이 예시 질문을 바로 전송합니다: “' + trimmedControlText(control) + '”'];
    }
    if (control.matches('.followup-chip')) {
      return ['후속 질문', '현재 답변의 맥락을 이어서 이 질문을 바로 전송합니다.'];
    }
    if (control.matches('.convo-action-btn')) {
      var action = control.getAttribute('title') || '관리';
      var actionText = {
        '고정': '이 대화를 목록 맨 위에 고정합니다.',
        '고정 해제': '목록 맨 위에 고정된 상태를 해제합니다.',
        '이름 변경': '이 대화의 목록 표시 이름을 변경합니다.',
        '삭제': '저장된 이 대화를 목록에서 삭제합니다.'
      };
      return ['대화 ' + action, actionText[action] || '이 대화의 설정을 변경합니다.'];
    }
    if (control.matches('.convo-item')) {
      return ['저장된 대화', '이 대화를 열어 이전 질문과 답변을 이어서 확인합니다.'];
    }
    if (control.matches('.db-ac-item, .db-ac-chip, .slash-source-item')) {
      return ['데이터 소스 선택', '이 소스를 선택하면 이번 질문의 조회 범위를 해당 데이터로 제한합니다.'];
    }
    if (control.matches('.chip-x')) {
      return ['데이터 소스 제외', '선택한 데이터 소스를 이번 질문의 조회 범위에서 제거합니다.'];
    }
    if (control.matches('.source-chip')) {
      return ['선택된 데이터 소스', '이번 질문은 이 칩에 표시된 데이터 소스만 조회합니다.'];
    }
    if (control.matches('.error-retry-btn')) {
      return ['다시 시도', '실패한 질문을 입력창에 다시 넣고 재전송합니다.'];
    }
    if (control.matches('.feedback-btn, .message-feedback-btn')) {
      return ['답변 평가', '이 답변이 도움이 되었는지 평가해 품질 개선에 반영합니다.'];
    }
    if (control.matches('.wiki-tab')) {
      return ['Wiki 보기 전환', 'Knowledge Wiki에서 “' + trimmedControlText(control) + '” 내용을 표시합니다.'];
    }

    var label = control.getAttribute('title') || control.getAttribute('aria-label');
    if (!label) {
      var text = trimmedControlText(control);
      if (text && text !== '×' && text !== '✕') label = text;
    }
    return label ? ['기능 안내', '“' + label + '” 기능을 실행합니다.'] : null;
  }

  function findControl(target) {
    if (!target || !target.closest) return null;
    return target.closest('.chip-x, button, a[href], input:not([type="hidden"]), textarea, [role="button"], .convo-item, .db-ac-item, .db-ac-chip, .source-chip, .slash-source-item, .source-filter-badge');
  }

  function guideControlEnter(event) {
    var control = findControl(event.target);
    var copy = controlGuide(control);
    if (control && copy) showControlGuide(control, copy);
  }

  function guideControlLeave(event) {
    var control = findControl(event.target);
    if (!control || activeGuideControl !== control) return;
    if (event.relatedTarget && control.contains(event.relatedTarget)) return;
    if (document.activeElement === control || control.contains(document.activeElement)) return;
    clearControlGuide(control);
  }

  function sourceNamesFromInput(value) {
    return (value.match(/@@[^\s]+/g) || []).map(function (token) {
      return token.substring(2).trim();
    }).filter(Boolean);
  }

  function activeSourceNames() {
    var names = [];
    var chips = document.querySelectorAll('#active-source-chips .source-chip');
    Array.prototype.forEach.call(chips, function (chip) {
      var name = chip.textContent.replace('×', '').replace(/^@@/, '').trim();
      if (name) names.push(name);
    });
    return names;
  }

  function formatSources(names) {
    if (names.length <= 2) return names.join(', ');
    return names.slice(0, 2).join(', ') + ' 외 ' + (names.length - 2) + '개';
  }

  function promptWithoutSources(value) {
    return value.replace(/@@[^\s]+\s*/g, '').trim();
  }

  function guidePrompt(value) {
    var lastAt = value.lastIndexOf('@@');
    var afterAt = lastAt >= 0 ? value.substring(lastAt + 2) : '';
    var choosingSource = lastAt >= 0 && afterAt.indexOf(' ') < 0;
    var selectedSources = sourceNamesFromInput(value);
    var prompt = promptWithoutSources(value);

    if (choosingSource) {
      setGuideFromPool('choosing-source', '데이터 소스 가이드', GUIDE_COPY.choosingSource);
      return;
    }
    if (selectedSources.length) {
      var selectedLabel = formatSources(selectedSources);
      setGuideFromPool('selected-source:' + selectedSources.join('|'), '데이터 소스 가이드', [
        selectedLabel + ' 데이터만 조회합니다. 다른 소스가 필요하면 @@를 이어서 입력하세요.',
        '이번 질문의 조회 범위는 ' + selectedLabel + '입니다. 선택하지 않은 소스는 함께 보지 않아요.',
        selectedLabel + '만 대상으로 질문합니다. 범위를 넓히려면 @@로 소스를 더 선택하세요.'
      ]);
      return;
    }
    if (!prompt) {
      setGuideFromPool('idle', '프롬프트 가이드', GUIDE_COPY.idle);
      return;
    }
    if (prompt.length < 8) {
      setGuideFromPool('short-prompt', '프롬프트 가이드', GUIDE_COPY.shortPrompt);
      return;
    }

    var dataIntent = /매출|판매|수량|주문|실적|roas|광고|리뷰|재고|순위|데이터|조회|비교|추이|증감/i.test(prompt);
    var hasPeriod = /\d{4}|오늘|어제|이번\s*(주|달|월|분기|년)|지난\s*(주|달|월|분기|년)|최근\s*\d+|[1-4]\s*분기|q[1-4]|메가와리/i.test(prompt);
    var hasScope = /국가|몰|채널|플랫폼|제품|sku|브랜드|팀|담당|캠페인|일별|주별|월별|분기별|연도별/i.test(prompt);
    var hasFormat = /표|차트|그래프|요약|목록|비교|추이|순위|증감률|퍼센트/i.test(prompt);

    if (dataIntent && !hasPeriod) {
      setGuideFromPool('missing-period', '프롬프트 가이드', GUIDE_COPY.missingPeriod);
    } else if (dataIntent && !hasScope) {
      setGuideFromPool('missing-scope', '프롬프트 가이드', GUIDE_COPY.missingScope);
    } else if (!hasFormat) {
      setGuideFromPool('missing-format', '프롬프트 가이드', GUIDE_COPY.missingFormat);
    } else {
      setGuideFromPool('ready', '프롬프트 가이드', GUIDE_COPY.ready);
    }
  }

  function bindEvents() {
    hideButton.addEventListener('click', function () {
      state.visible = false;
      render();
      saveState();
    });
    summonButton.addEventListener('click', function () {
      state.visible = true;
      activeGuideControl = null;
      render();
      place();
      guidePrompt(input ? input.value : '');
      saveState();
    });
    window.addEventListener('resize', place);
    document.addEventListener('pointerover', guideControlEnter);
    document.addEventListener('pointerout', guideControlLeave);
    document.addEventListener('focusin', guideControlEnter);
    document.addEventListener('focusout', guideControlLeave);
    if (input) {
      input.addEventListener('focus', function () {
        activeGuideControl = null;
        guidePrompt(input.value);
      });
      input.addEventListener('input', function () {
        activeGuideControl = null;
        guidePrompt(input.value);
      });
    }
  }

  function touchesSourceUi(record) {
    var target = record.target && record.target.nodeType === 1 ? record.target : record.target.parentElement;
    if (target && target.closest && target.closest('#active-source-chips, .db-autocomplete-dropdown')) return true;
    var found = false;
    Array.prototype.forEach.call(record.addedNodes || [], function (node) {
      if (found || node.nodeType !== 1) return;
      found = node.matches('#active-source-chips, .source-chip, .db-autocomplete-dropdown') ||
        !!node.querySelector('#active-source-chips, .source-chip, .db-autocomplete-dropdown');
    });
    return found;
  }

  function watchInterface() {
    if (!main || !window.MutationObserver) return;
    observer = new MutationObserver(function (records) {
      var hasError = false;
      var sourceUiChanged = false;
      records.forEach(function (record) {
        sourceUiChanged = sourceUiChanged || touchesSourceUi(record);
        Array.prototype.forEach.call(record.addedNodes || [], function (node) {
          if (node.nodeType !== 1) return;
          hasError = hasError || node.matches('.error-card') || !!node.querySelector('.error-card');
        });
      });

      var streaming = !!messages.querySelector('.message.streaming, .typing-indicator');
      if (hasError) {
        activeGuideControl = null;
        setGuideFromPool('error', '다시 시도 가이드', GUIDE_COPY.error);
      } else if (streaming) {
        activeGuideControl = null;
        var sources = activeSourceNames();
        if (sources.length) {
          var streamingLabel = formatSources(sources);
          setGuideFromPool('streaming-source:' + sources.join('|'), '데이터 소스 가이드', [
            '이번 답변은 ' + streamingLabel + ' 데이터만 조회하고 있어요.',
            streamingLabel + ' 범위에서 답을 찾고 있어요. 선택하지 않은 소스는 조회하지 않아요.',
            '선택한 ' + streamingLabel + ' 데이터만 확인 중이에요.'
          ]);
        } else {
          setGuideFromPool('streaming', '답변 활용 가이드', GUIDE_COPY.streaming);
        }
      } else if (wasStreaming) {
        activeGuideControl = null;
        setGuideFromPool('after-answer', '답변 활용 가이드', GUIDE_COPY.afterAnswer);
      } else if (sourceUiChanged) {
        activeGuideControl = null;
        guidePrompt(input ? input.value : '');
      }
      wasStreaming = streaming;
      if (sourceUiChanged) place();
    });
    observer.observe(main, { childList: true, subtree: true, attributes: true, attributeFilter: ['class', 'style'] });
  }

  function cleanup() {
    saveState();
    if (observer) observer.disconnect();
    document.removeEventListener('pointerover', guideControlEnter);
    document.removeEventListener('pointerout', guideControlLeave);
    document.removeEventListener('focusin', guideControlEnter);
    document.removeEventListener('focusout', guideControlLeave);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', create, { once: true });
  else create();
  window.addEventListener('pagehide', cleanup, { once: true });
}());
