/* Browser speech-to-text for every AI composer.
 *
 * Home uses [data-hvoice]; Project / daily / weekly use [data-mic].
 * Recognition is browser-side: the audio is not uploaded by this app.
 */
(function () {
  'use strict';

  var Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  var recognition = null;
  var activeButton = null;
  var activeInput = null;
  var baseText = '';

  function toast(message) {
    if (typeof window.toast === 'function') {
      window.toast(message);
      return;
    }
    var el = document.getElementById('toast');
    if (!el) return;
    el.textContent = message;
    el.classList.remove('hidden');
    clearTimeout(el._voiceTimer);
    el._voiceTimer = setTimeout(function () {
      el.classList.add('hidden');
    }, 2600);
  }

  function currentInput(button) {
    var form = button.closest('form.composer');
    if (form) return form.querySelector('input');
    var panel = button.closest('.hai, .ai');
    return panel ? panel.querySelector('form.composer input') : null;
  }

  function resetButton() {
    if (activeButton) {
      activeButton.classList.remove('voice-on');
      activeButton.setAttribute('aria-pressed', 'false');
      activeButton.setAttribute('title', '语音输入（浏览器转写）');
    }
    activeButton = null;
    activeInput = null;
    recognition = null;
    baseText = '';
  }

  function stopRecognition() {
    var current = recognition;
    recognition = null;
    if (current) {
      try {
        current.onend = null;
        current.onresult = null;
        current.onerror = null;
        current.abort();
      } catch (_error) {
        /* already stopped */
      }
    }
    resetButton();
  }

  function micError(error) {
    var name = (error && error.name) || '';
    if (name === 'NotAllowedError' || name === 'SecurityError') {
      return '浏览器已阻止麦克风。点地址栏左侧站点图标 → 麦克风 → 允许，刷新后重试。';
    }
    if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
      return '没有找到麦克风，请检查设备连接。';
    }
    if (name === 'NotReadableError' || name === 'TrackStartError') {
      return '麦克风被其他应用占用，请关闭占用程序后重试。';
    }
    return '无法打开麦克风：' + (error && error.message ? error.message : name || '未知错误');
  }

  function beginRecognition(button, input) {
    recognition = new Recognition();
    recognition.lang = 'zh-CN';
    recognition.interimResults = true;
    recognition.continuous = false;
    recognition.maxAlternatives = 1;

    recognition.onresult = function (event) {
      var interim = '';
      var finalText = '';
      for (var i = event.resultIndex; i < event.results.length; i += 1) {
        var result = event.results[i];
        var transcript = result[0] ? result[0].transcript : '';
        if (result.isFinal) finalText += transcript;
        else interim += transcript;
      }
      if (finalText.trim()) {
        baseText = (baseText ? baseText + ' ' : '') + finalText.trim();
      }
      var value = (baseText + (interim.trim() ? (baseText ? ' ' : '') + interim.trim() : '')).trim();
      var inputNow = activeInput && activeInput.isConnected
        ? activeInput
        : document.querySelector('#homeComposer input, .ai .composer input');
      if (inputNow && value) {
        inputNow.value = value;
        inputNow.dispatchEvent(new Event('input', { bubbles: true }));
      }
    };

    recognition.onerror = function (event) {
      if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
        toast('麦克风/语音识别被浏览器拒绝。点地址栏左侧站点图标 → 麦克风 → 允许，刷新后重试。');
      } else if (event.error === 'no-speech') {
        toast('没有听到声音，请靠近麦克风再试一次。');
      } else if (event.error !== 'aborted') {
        toast('语音识别失败：' + event.error);
      }
    };

    recognition.onend = function () {
      var inputNow = activeInput && activeInput.isConnected
        ? activeInput
        : document.querySelector('#homeComposer input, .ai .composer input');
      resetButton();
      if (inputNow) inputNow.focus();
    };

    try {
      recognition.start();
    } catch (error) {
      resetButton();
      toast('无法启动语音识别：' + (error.message || error));
    }
  }

  function startRecognition(button, input) {
    if (!Recognition) {
      toast('当前浏览器不支持语音识别，请使用 Edge 或 Chrome 打开。');
      return;
    }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      toast('当前页面没有麦克风权限接口，请通过 http://127.0.0.1:8000 打开。');
      return;
    }

    stopRecognition();
    activeButton = button;
    activeInput = input;
    baseText = (input.value || '').trim();
    button.classList.add('voice-on');
    button.setAttribute('aria-pressed', 'true');
    button.setAttribute('title', '正在请求麦克风权限…');
    input.focus();

    navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
      stream.getTracks().forEach(function (track) { track.stop(); });
      if (activeButton !== button) return;
      button.setAttribute('title', '正在听，再点一下停止');
      beginRecognition(button, input);
    }).catch(function (error) {
      if (activeButton === button) resetButton();
      toast(micError(error));
    });
  }

  document.addEventListener('click', function (event) {
    var button = event.target.closest && event.target.closest('[data-hvoice], [data-mic]');
    if (!button) return;
    event.preventDefault();
    event.stopImmediatePropagation();

    if (recognition && activeButton === button) {
      stopRecognition();
      return;
    }
    if (recognition) stopRecognition();

    var input = currentInput(button);
    if (!input) {
      toast('找不到语音输入框，请刷新页面再试。');
      return;
    }
    if (button.classList.contains('voice-on')) {
      stopRecognition();
      return;
    }
    startRecognition(button, input);
  }, true);
})();
