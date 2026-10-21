package com.seoulbike.backend.ref;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Arrays;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.core.io.ClassPathResource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * 구역·대여소 기준 정보(analysis/out/ref_*.csv, 6번이 매주 갱신)를 ref.regions · ref.stations에 올린다.
 * 빌드가 CSV를 jar의 ref/ 아래에 넣고(build.gradle refDataDir), 시작할 때 한 트랜잭션으로 올린다.
 * 파일이 지난번과 같으면 건너뛴다. 파일에서 빠진 대여소는 지우지 않고 active=false로 둔다 (지난 계획이 가리킨다, 합의 8).
 */
@Component
class RefDataLoader implements ApplicationRunner {

	private static final Logger log = LoggerFactory.getLogger(RefDataLoader.class);

	static final String REGIONS_FILE = "ref_regions.csv";
	static final String STATIONS_FILE = "ref_stations.csv";

	/** 올린 결과. changedStations는 새로 생기거나 내용이 바뀐(다시 활성 포함) 대여소 수다. */
	record Result(int regions, int stations, int changedStations, int deactivatedStations) {
	}

	private final JdbcClient jdbc;
	private final JdbcTemplate batch;
	private final TransactionTemplate tx;

	RefDataLoader(JdbcClient jdbc, JdbcTemplate batch, TransactionTemplate tx) {
		this.jdbc = jdbc;
		this.batch = batch;
		this.tx = tx;
	}

	@Override
	public void run(ApplicationArguments args) {
		String regions = classpath(REGIONS_FILE);
		String stations = classpath(STATIONS_FILE);
		try {
			loadIfChanged(regions, stations);
		}
		catch (RuntimeException e) {
			// 주간 파일 하나가 깨졌다고 서비스 전체를 내리지 않는다. 트랜잭션이 되돌려져 지난주 기준 정보가 그대로 남는다.
			log.error("기준 정보 CSV를 올리지 못했다. 이전 기준 정보를 그대로 쓴다", e);
		}
	}

	/** 두 파일 내용이 지난번에 올린 것과 같으면 아무것도 하지 않고 null을 돌려준다. */
	Result loadIfChanged(String regionsCsv, String stationsCsv) {
		if (sha256(regionsCsv).equals(loadedSha(REGIONS_FILE)) && sha256(stationsCsv).equals(loadedSha(STATIONS_FILE))) {
			log.info("기준 정보 파일이 지난번과 같아 건너뛴다: 구역 {}개, 대여소 {}개(활성 {}개)", count("ref.regions", false),
					count("ref.stations", false), count("ref.stations", true));
			return null;
		}
		return load(regionsCsv, stationsCsv);
	}

	/** 파일 내용과 상관없이 올린다. 같은 파일을 여러 번 올려도 결과가 같다. */
	Result load(String regionsCsv, String stationsCsv) {
		List<Map<String, String>> regionRows = RefCsv.parse(regionsCsv);
		List<Map<String, String>> stationRows = RefCsv.parse(stationsCsv);
		Result result = tx.execute(status -> {
			upsertRegions(regionRows);
			int changed = upsertStations(stationRows);
			String[] ids = stationRows.stream().map(r -> r.get("api_station_id")).toArray(String[]::new);
			int deactivated = jdbc.sql("""
					update ref.stations set active = false, updated_at = now()
					where active and station_id <> all(:ids)
					""")
				.param("ids", ids)
				.update();
			recordSha(REGIONS_FILE, regionsCsv);
			recordSha(STATIONS_FILE, stationsCsv);
			return new Result(regionRows.size(), stationRows.size(), changed, deactivated);
		});
		log.info("기준 정보를 올렸다: 구역 {}개, 대여소 {}개 (새로 생기거나 바뀐 대여소 {}개, 파일에서 빠져 비활성으로 돌린 대여소 {}개)",
				result.regions(), result.stations(), result.changedStations(), result.deactivatedStations());
		return result;
	}

	private void upsertRegions(List<Map<String, String>> rows) {
		batch.batchUpdate("""
				insert into ref.regions as r (region_id, name, side, stations, depot_lat, depot_lon, trucks_min)
				values (?, ?, ?, ?, ?, ?, ?)
				on conflict (region_id) do update set
					name = excluded.name, side = excluded.side, stations = excluded.stations,
					depot_lat = excluded.depot_lat, depot_lon = excluded.depot_lon, trucks_min = excluded.trucks_min,
					updated_at = now()
				where (r.name, r.side, r.stations, r.depot_lat, r.depot_lon, r.trucks_min)
					is distinct from (excluded.name, excluded.side, excluded.stations, excluded.depot_lat, excluded.depot_lon, excluded.trucks_min)
				""", rows.stream()
			.map(r -> new Object[] { r.get("region_id"), r.get("name"), r.get("side"), Integer.valueOf(r.get("stations")),
					Double.valueOf(r.get("depot_lat")), Double.valueOf(r.get("depot_lon")),
					Integer.valueOf(r.get("trucks_min")) })
			.toList());
	}

	/** CSV 열 대응: api_station_id(ST-4) → station_id, station_id(대여소번호 102) → station_no. */
	private int upsertStations(List<Map<String, String>> rows) {
		int[] counts = batch.batchUpdate("""
				insert into ref.stations as s (station_id, station_no, name, region_id, district, address, lat, lon, docks, elevation_m, role, active)
				values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, true)
				on conflict (station_id) do update set
					station_no = excluded.station_no, name = excluded.name, region_id = excluded.region_id,
					district = excluded.district, address = excluded.address, lat = excluded.lat, lon = excluded.lon,
					docks = excluded.docks, elevation_m = excluded.elevation_m, role = excluded.role, active = true,
					updated_at = now()
				where (s.station_no, s.name, s.region_id, s.district, s.address, s.lat, s.lon, s.docks, s.elevation_m, s.role, s.active)
					is distinct from (excluded.station_no, excluded.name, excluded.region_id, excluded.district, excluded.address,
						excluded.lat, excluded.lon, excluded.docks, excluded.elevation_m, excluded.role, true)
				""", rows.stream()
			.map(r -> new Object[] { r.get("api_station_id"), r.get("station_id"), r.get("name"), r.get("region_id"),
					r.get("district"), r.get("address"), Double.valueOf(r.get("lat")), Double.valueOf(r.get("lon")),
					Integer.valueOf(r.get("docks")), new BigDecimal(r.get("elevation_m")), r.get("role") })
			.toList());
		return Arrays.stream(counts).sum();
	}

	private String loadedSha(String fileName) {
		return jdbc.sql("select sha256 from ref.source_files where file_name = :f")
			.param("f", fileName)
			.query(String.class)
			.optional()
			.orElse(null);
	}

	private void recordSha(String fileName, String content) {
		jdbc.sql("""
				insert into ref.source_files (file_name, sha256) values (:f, :sha)
				on conflict (file_name) do update set sha256 = excluded.sha256, loaded_at = now()
				""")
			.param("f", fileName)
			.param("sha", sha256(content))
			.update();
	}

	private int count(String table, boolean activeOnly) {
		return jdbc.sql("select count(*) from " + table + (activeOnly ? " where active" : ""))
			.query(Integer.class)
			.single();
	}

	static String classpath(String fileName) {
		var resource = new ClassPathResource("ref/" + fileName);
		try {
			return resource.getContentAsString(StandardCharsets.UTF_8);
		}
		catch (IOException e) {
			// 빌드가 analysis/out을 찾지 못한 경우다 (build.gradle refDataDir, compose additional_contexts refdata).
			throw new UncheckedIOException("jar에 ref/" + fileName + "이 없다", e);
		}
	}

	private static String sha256(String content) {
		try {
			return HexFormat.of()
				.formatHex(MessageDigest.getInstance("SHA-256").digest(content.getBytes(StandardCharsets.UTF_8)));
		}
		catch (NoSuchAlgorithmException e) {
			throw new IllegalStateException(e);
		}
	}
}
