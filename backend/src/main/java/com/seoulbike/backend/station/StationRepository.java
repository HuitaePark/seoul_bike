package com.seoulbike.backend.station;

import java.math.BigDecimal;
import java.sql.Array;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.Arrays;
import java.util.Collection;
import java.util.List;
import java.util.Optional;
import java.util.Set;

import com.fasterxml.jackson.annotation.JsonInclude;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/**
 * 대여소 기준 정보(ref, V10) · 실시간 재고(live, V11) · 예측(forecast, V11)을 읽는다. 셋 다 backend는 읽기만 한다.
 * live·forecast는 ref로 FK가 없으므로(V11) 대여소가 있는지·운영 중인지는 여기서 ref.stations로 확인한다.
 */
@Repository
class StationRepository {

	/**
	 * 운영 중인 대여소와 가장 최근 관측, 그리고 고른 예측 구간. 관측이 없는 대여소는 넣지 않는다 (재고가 없으면 위험을 판단할 수 없다).
	 * 예측 구간: horizon_h가 같고 아직 끝나지 않은(start + H시간 > 지금) 행 중 start가 가장 이른 것. 진행 중인 근무조의 예측이 있으면
	 * 그것, 없으면 다음 근무조 예측(시작 30분 전에 들어온다)이다. 지난 근무조의 예측은 쓰지 않는다. 같은 start가 둘이면 나중에 넣은 것.
	 */
	private static final String OBSERVED = """
			select s.station_id, s.name, s.district, s.lat, s.lon, s.elevation_m, s.role,
			       o.bikes, o.docks, o.observed_at,
			       f.start, f.rentals, f.returns, f.rentals_h, f.returns_h
			from ref.stations s
			cross join lateral (
			    select bikes, docks, observed_at from live.station_status
			    where station_id = s.station_id
			    order by observed_at desc
			    limit 1) o
			left join lateral (
			    select start, rentals, returns, rentals_h, returns_h from forecast.station_forecasts
			    where station_id = s.station_id and horizon_h = :h and start > now() - make_interval(hours => :h)
			    order by start, created_at desc
			    limit 1) f on true
			where s.active and """;

	/** 운영 중인 대여소 하나의 가장 최근 관측과 고른 예측 구간(없으면 null). */
	record Observed(String stationId, String name, String district, double lat, double lon, Double elevationM,
			String role, int bikes, int docks, Instant observedAt, StationRisk.Window window) {
	}

	/** 기준 정보만 (운영 여부와 상관없이). 없는 대여소 오류에 후보를 붙일 때 쓴다. */
	record Ref(String stationId, String name, String regionId, double lat, double lon, boolean active) {
	}

	/** 계약 internal-api Forecast. 시간별 값이 없으면 필드를 빼고 보낸다 (계약: "내지 않으면 없다"). */
	record Forecast(String stationId, Instant start, int horizonH, BigDecimal rentals, BigDecimal returns,
			@JsonInclude(JsonInclude.Include.NON_NULL) List<BigDecimal> rentalsH,
			@JsonInclude(JsonInclude.Include.NON_NULL) List<BigDecimal> returnsH, String modelVersion) {
	}

	private final JdbcClient jdbc;

	StationRepository(JdbcClient jdbc) {
		this.jdbc = jdbc;
	}

	List<String> regionIds() {
		return jdbc.sql("select region_id from ref.regions order by region_id").query(String.class).list();
	}

	List<Observed> observedInRegion(String regionId, int horizonH) {
		return jdbc.sql(OBSERVED + " s.region_id = :regionId order by s.station_id")
			.param("h", horizonH)
			.param("regionId", regionId)
			.query(StationRepository::observed)
			.list();
	}

	Optional<Observed> observed(String stationId, int horizonH) {
		return jdbc.sql(OBSERVED + " s.station_id = :stationId")
			.param("h", horizonH)
			.param("stationId", stationId)
			.query(StationRepository::observed)
			.optional();
	}

	Optional<Ref> find(String stationId) {
		return jdbc.sql("select station_id, name, region_id, lat, lon, active from ref.stations where station_id = :id")
			.param("id", stationId)
			.query((rs, i) -> new Ref(rs.getString("station_id"), rs.getString("name"), rs.getString("region_id"),
					rs.getDouble("lat"), rs.getDouble("lon"), rs.getBoolean("active")))
			.optional();
	}

	Set<String> activeAmong(Collection<String> stationIds) {
		return Set.copyOf(jdbc.sql("select station_id from ref.stations where active and station_id in (:ids)")
			.param("ids", stationIds)
			.query(String.class)
			.list());
	}

	/** 같은 구역의 운영 중인 대여소를 가까운 순으로. 위도 1도와 경도 1도의 거리 차이는 cos(위도)로 맞춘다. */
	List<String> nearestActive(String regionId, double lat, double lon, int limit) {
		return jdbc.sql("""
				select station_id from ref.stations
				where active and region_id = :regionId
				order by power(lat - :lat, 2) + power((lon - :lon) * cos(radians(:lat)), 2), station_id
				limit :limit
				""")
			.param("regionId", regionId)
			.param("lat", lat)
			.param("lon", lon)
			.param("limit", limit)
			.query(String.class)
			.list();
	}

	/** 대여소번호(station_no)가 이 값인 운영 중인 대여소. 번호를 ID로 잘못 넣었을 때 알려 준다 (번호를 문자열로 바꾸지 않고 표에서 찾는다). */
	List<String> activeByStationNo(String stationNo) {
		return jdbc.sql("select station_id from ref.stations where active and station_no = :no order by station_id")
			.param("no", stationNo)
			.query(String.class)
			.list();
	}

	List<String> activeByNameFragment(String fragment, int limit) {
		return jdbc.sql("""
				select station_id from ref.stations
				where active and strpos(name, :q) > 0
				order by station_id
				limit :limit
				""")
			.param("q", fragment)
			.param("limit", limit)
			.query(String.class)
			.list();
	}

	/** start·horizon_h가 정확히 같은 행만. 대여소마다 하나 (같은 값이 둘이면 나중에 넣은 것). */
	List<Forecast> forecasts(Collection<String> stationIds, Instant start, int horizonH) {
		return jdbc.sql("""
				select distinct on (station_id) station_id, start, horizon_h, rentals, returns, rentals_h, returns_h,
				       model_version
				from forecast.station_forecasts
				where station_id in (:ids) and start = :start and horizon_h = :h
				order by station_id, created_at desc
				""")
			.param("ids", stationIds)
			.param("start", OffsetDateTime.ofInstant(start, ZoneOffset.UTC))
			.param("h", horizonH)
			.query((rs, i) -> new Forecast(rs.getString("station_id"),
					rs.getObject("start", OffsetDateTime.class).toInstant(), rs.getInt("horizon_h"),
					rs.getBigDecimal("rentals"), rs.getBigDecimal("returns"), decimals(rs.getArray("rentals_h")),
					decimals(rs.getArray("returns_h")), rs.getString("model_version")))
			.list();
	}

	private static Observed observed(ResultSet rs, int row) throws SQLException {
		OffsetDateTime start = rs.getObject("start", OffsetDateTime.class);
		StationRisk.Window window = start == null ? null
				: new StationRisk.Window(start.toInstant(), rs.getDouble("rentals"), rs.getDouble("returns"),
						doubles(rs.getArray("rentals_h")), doubles(rs.getArray("returns_h")));
		BigDecimal elevation = rs.getBigDecimal("elevation_m");
		return new Observed(rs.getString("station_id"), rs.getString("name"), rs.getString("district"),
				rs.getDouble("lat"), rs.getDouble("lon"), elevation == null ? null : elevation.doubleValue(),
				rs.getString("role"), rs.getInt("bikes"), rs.getInt("docks"),
				rs.getObject("observed_at", OffsetDateTime.class).toInstant(), window);
	}

	private static List<BigDecimal> decimals(Array array) throws SQLException {
		return array == null ? null : Arrays.asList((BigDecimal[]) array.getArray());
	}

	private static double[] doubles(Array array) throws SQLException {
		List<BigDecimal> values = decimals(array);
		return values == null ? null : values.stream().mapToDouble(BigDecimal::doubleValue).toArray();
	}
}
