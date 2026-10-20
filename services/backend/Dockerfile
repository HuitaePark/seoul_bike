# 서버(Apple Silicon)에서 직접 빌드한다. eclipse-temurin은 arm64 이미지를 제공한다.
FROM eclipse-temurin:21-jdk AS build
WORKDIR /app
# 의존성 층을 먼저 받아 두면 소스만 바뀐 재빌드가 빨라진다.
COPY gradlew settings.gradle build.gradle ./
COPY gradle gradle
RUN ./gradlew --no-daemon dependencies > /dev/null
COPY src src
RUN ./gradlew --no-daemon bootJar -x test

FROM eclipse-temurin:21-jre
WORKDIR /app
RUN useradd --system --uid 10001 app
COPY --from=build /app/build/libs/backend.jar app.jar
ARG GIT_SHA=local
ENV GIT_SHA=${GIT_SHA}
USER app
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "/app/app.jar"]
