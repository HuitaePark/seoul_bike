<script setup lang="ts">
// 로그인·회원가입 공통 틀. 왼쪽에 회사명과 폼, 오른쪽에 재배치 장면 일러스트.
import { companyName } from '../../shared/labels'
import CityScene from './CityScene.vue'
</script>

<template>
  <div class="auth">
    <section class="panel">
      <header class="identity">
        <span class="mark" aria-hidden="true">
          <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="5.5" cy="17" r="3.5" /><circle cx="18.5" cy="17" r="3.5" /><path d="M5.5 17 9 9h6l3.5 8M9 9 12 17h1.5M14 5h2.5" />
          </svg>
        </span>
        <div>
          <strong class="company">{{ companyName }}</strong>
          <span class="product">따릉이 재배치 에이전트</span>
        </div>
      </header>
      <p class="tagline">계획은 에이전트가 세우고, 결정은 사람이 내린다.</p>

      <div class="body"><slot /></div>

      <footer class="copy">COPYRIGHT © 2026 {{ companyName }}. ALL RIGHTS RESERVED.</footer>
    </section>

    <div class="art">
      <CityScene />
      <ul class="legend" aria-label="대여소 상태 색">
        <li><i style="background: var(--empty)" />부족</li>
        <li><i style="background: var(--overflow)" />과잉</li>
        <li><i style="background: var(--ok)" />정상</li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.auth {
  min-height: 100vh;
  display: grid;
  grid-template-columns: minmax(360px, 460px) minmax(0, 1fr);
  background: linear-gradient(180deg, #bfe1f4 0%, #dff0fa 55%, #f2f8fc 100%);
}
.panel { display: flex; flex-direction: column; padding: 48px 56px 32px; }
.identity { display: flex; align-items: center; gap: 14px; margin-top: auto; } /* 폼 묶음을 세로 가운데에 */
.mark { flex: none; width: 52px; height: 52px; border-radius: 15px; background: var(--brand); display: grid; place-items: center; box-shadow: 0 6px 16px rgb(15 157 88 / 0.3); }
.company { display: block; font-size: 30px; font-weight: 800; letter-spacing: -0.02em; color: var(--ink); line-height: 1.15; }
.product { display: block; font-size: 15px; font-weight: 600; color: var(--brand-strong); margin-top: 2px; }
.tagline { margin: 18px 0 32px; font-size: 15px; color: var(--ink-2); }
.body { margin-bottom: auto; }
.copy { margin-top: 40px; font-size: 11px; color: var(--ink-3); letter-spacing: 0.02em; }

.art { position: relative; display: flex; align-items: center; justify-content: center; padding: 24px 24px 24px 0; min-width: 0; }
.art :deep(.scene) { max-height: calc(100vh - 48px); }
.legend { position: absolute; right: 32px; bottom: 28px; display: flex; gap: 14px; margin: 0; padding: 8px 14px; list-style: none; font-size: 12px; font-weight: 600; color: var(--ink-2); background: rgb(255 255 255 / 0.85); border-radius: 999px; box-shadow: var(--shadow); }
.legend li { display: flex; align-items: center; gap: 6px; }
.legend i { width: 9px; height: 9px; border-radius: 50%; }

/* 좁은 화면(기사 휴대폰 포함): 위는 그림, 아래는 흰 시트에 폼. 내용은 가운데 한 줄(최대 440px)로 맞춘다 */
@media (max-width: 900px) {
  .auth { grid-template-columns: minmax(0, 1fr); grid-template-rows: auto 1fr; }
  .art { order: -1; flex-direction: column; gap: 8px; padding: 16px 16px 20px; }
  .art :deep(.scene) { width: 100%; max-width: 400px; height: auto; max-height: 34vh; }
  .legend { position: static; padding: 6px 12px; font-size: 11px; }
  .panel { align-items: center; padding: 28px 20px 20px; background: #fff; border-radius: 24px 24px 0 0; box-shadow: 0 -8px 24px rgb(15 23 42 / 0.06); }
  .panel > * { width: 100%; max-width: 440px; }
  .body :slotted(.auth-form) { max-width: none; }
  .identity { margin-top: 0; }
  .company { font-size: 26px; }
  .tagline { margin: 12px 0 22px; }
  .body { margin-bottom: 0; }
  .copy { margin-top: auto; padding-top: 32px; }
}
</style>
