'use strict';

// token 仅用于换取 HttpOnly 会话；不存入 URL 或浏览器持久存储。
document.getElementById('login-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const input = document.getElementById('token');
  const button = event.currentTarget.querySelector('button');
  const error = document.getElementById('login-error');
  button.disabled = true;
  error.textContent = '';
  try {
    const response = await fetch('/import/v1/session', {
      method: 'POST',
      headers: { Authorization: 'Bearer ' + input.value, 'Content-Type': 'application/json' },
      body: '{}',
      cache: 'no-store'
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.code + '：' + result.error);
    window.location.replace('/import-review/');
  } catch (failure) {
    error.textContent = failure.message;
  } finally {
    input.value = '';
    button.disabled = false;
  }
});
