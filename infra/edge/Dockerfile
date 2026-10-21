# 서버 포트를 잡는 앞단 (infra/edge/nginx.conf). 설정을 이미지에 넣어, 설정이 바뀌면 이미지가 바뀌어 다시 만들어진다
# (파일을 볼륨으로 붙이면 내용이 바뀌어도 compose가 모르고 옛 설정으로 돈다). frontend와 같은 nginx 안정판 계열이다.
FROM nginx:1.30-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
