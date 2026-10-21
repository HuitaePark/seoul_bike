package com.seoulbike.backend.ref;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.Map;
import java.util.stream.Collectors;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.dao.DataAccessException;
import org.springframework.jdbc.core.simple.JdbcClient;

import com.seoulbike.backend.IntegrationTest;

/** 시작할 때 analysis/out/ref_*.csv가 ref.regions · ref.stations에 올라가는지. 다른 화면·API가 이 표를 기준으로 쓴다. */
@IntegrationTest
class RefDataLoaderTest {

	private static final String REGIONS = RefDataLoader.classpath(RefDataLoader.REGIONS_FILE);
	private static final String STATIONS = RefDataLoader.classpath(RefDataLoader.STATIONS_FILE);

	@Autowired
	RefDataLoader loader;

	@Autowired
	JdbcClient jdbc;

	@Test
	void 시작하면_서울_전체_17개_구역과_2716개_대여소가_올라간다() {
		assertThat(count("select count(*) from ref.regions")).isEqualTo(17);
		assertThat(count("select count(*) from ref.stations")).isEqualTo(2716);
		assertThat(jdbc.sql("select region_id from ref.regions order by region_id").query(String.class).list())
			.containsExactly("N01", "N02", "N03", "N04", "N05", "N06", "N07", "N08", "S01", "S02", "S03", "S04", "S05",
					"S06", "S07", "S08", "S09");
	}

	@Test
	void 대여소_ID는_실시간_API_ID이고_대여소번호는_따로_둔다() {
		// 두 번호 체계는 다르다: 번호 102 = ST-4, ST-102 = 번호 505. CSV의 station_id 열은 번호다.
		assertThat(station("ST-4")).containsEntry("station_no", "102")
			.containsEntry("name", "망원역 1번출구 앞")
			.containsEntry("region_id", "N04")
			.containsEntry("active", true);
		assertThat(station("ST-102")).containsEntry("station_no", "505").containsEntry("name", "자양사거리 광진아크로텔 앞");
		// 이름 안의 쉼표(따옴표로 감싼 칸)에서 열이 밀리지 않는다.
		assertThat(station("ST-1999")).containsEntry("name", "염창역 3, 4번 출구 사이").containsEntry("region_id", "S08");
	}

	@Test
	void 새_파일에서_빠진_대여소는_지우지_않고_비활성으로_두며_다시_들어오면_활성이_된다() {
		String withoutSt4 = STATIONS.lines()
			.filter(line -> !line.startsWith("102,ST-4,"))
			.collect(Collectors.joining("\n"));
		try {
			var result = loader.load(REGIONS, withoutSt4);

			assertThat(result.deactivatedStations()).isEqualTo(1);
			// 지난 계획이 ST-4를 가리키므로 행과 속성은 남는다.
			assertThat(station("ST-4")).containsEntry("active", false).containsEntry("station_no", "102");
			assertThat(count("select count(*) from ref.stations")).isEqualTo(2716);
			assertThat(count("select count(*) from ref.stations where active")).isEqualTo(2715);
		}
		finally {
			var back = loader.load(REGIONS, STATIONS);
			assertThat(back.changedStations()).isEqualTo(1);
		}
		assertThat(station("ST-4")).containsEntry("active", true);
		assertThat(count("select count(*) from ref.stations where active")).isEqualTo(2716);
	}

	@Test
	void 같은_파일을_다시_올려도_결과가_같고_시작할_때는_건너뛴다() {
		loader.load(REGIONS, STATIONS);
		var before = jdbc.sql("select max(updated_at)::text from ref.stations").query(String.class).single();

		var again = loader.load(REGIONS, STATIONS);

		assertThat(again).isEqualTo(new RefDataLoader.Result(17, 2716, 0, 0));
		assertThat(jdbc.sql("select max(updated_at)::text from ref.stations").query(String.class).single())
			.isEqualTo(before);
		assertThat(count("select count(*) from ref.stations where active")).isEqualTo(2716);
		// 다음 재시작(같은 jar)은 파일이 같으므로 아무것도 하지 않는다.
		assertThat(loader.loadIfChanged(REGIONS, STATIONS)).isNull();
	}

	@Test
	void 깨진_파일은_통째로_되돌리고_해시를_남기지_않아_다음_시작_때_다시_시도한다() {
		String shaBefore = stationsSha();
		// 앞쪽 행(ST-4 이름)은 바꾸고 맨 끝 행은 역할 값이 틀렸다. 앞쪽 변경까지 남지 않아야 한다.
		String broken = STATIONS.lines()
			.map(line -> line.startsWith("102,ST-4,") ? line.replace("망원역 1번출구 앞", "바뀐 이름") : line)
			.collect(Collectors.joining("\n"))
				+ "\n99998,ST-99998,깨진 행,N04,마포구,주소,37.5,126.9,10,1.0,BROKEN\n";

		assertThatThrownBy(() -> loader.loadIfChanged(REGIONS, broken)).isInstanceOf(DataAccessException.class);

		assertThat(station("ST-4")).containsEntry("name", "망원역 1번출구 앞");
		assertThat(count("select count(*) from ref.stations where station_id = 'ST-99998'")).isZero();
		assertThat(stationsSha()).isEqualTo(shaBefore);
	}

	@Test
	void 두_대여소의_번호가_맞바뀐_파일도_올라간다() {
		// 행마다 바로 검사하면 첫 행에서 103이 겹친다. 커밋 때 검사하므로 올라가야 한다.
		String swapped = STATIONS.lines()
			.map(line -> line.startsWith("102,ST-4,") ? "103" + line.substring(3)
					: line.startsWith("103,ST-5,") ? "102" + line.substring(3) : line)
			.collect(Collectors.joining("\n"));
		try {
			assertThat(loader.load(REGIONS, swapped).changedStations()).isEqualTo(2);
			assertThat(station("ST-4")).containsEntry("station_no", "103");
			assertThat(station("ST-5")).containsEntry("station_no", "102");
		}
		finally {
			loader.load(REGIONS, STATIONS);
		}
		assertThat(station("ST-4")).containsEntry("station_no", "102");
	}

	@Test
	void 비활성_대여소의_번호를_새_대여소가_써도_올라간다() {
		// ST-4가 빠지고 같은 번호 102가 새 ST- ID로 들어온 주. 지난 계획이 가리키는 ST-4는 번호를 가진 채 비활성으로 남는다.
		String reused = STATIONS.lines()
			.map(line -> line.startsWith("102,ST-4,") ? "102,ST-99999" + line.substring("102,ST-4".length()) : line)
			.collect(Collectors.joining("\n"));
		try {
			var result = loader.load(REGIONS, reused);

			assertThat(result.deactivatedStations()).isEqualTo(1);
			assertThat(station("ST-4")).containsEntry("station_no", "102").containsEntry("active", false);
			assertThat(station("ST-99999")).containsEntry("station_no", "102").containsEntry("active", true);
		}
		finally {
			// 테스트끼리 DB를 같이 쓰므로 원래 파일로 되돌리고 시험용 대여소를 지운다.
			loader.load(REGIONS, STATIONS);
			jdbc.sql("delete from ref.stations where station_id = 'ST-99999'").update();
		}
		assertThat(station("ST-4")).containsEntry("active", true);
	}

	@Test
	void 파일이_같아도_표가_비어_있으면_다시_올린다() {
		loader.load(REGIONS, STATIONS);
		// DB를 초기화하거나 손으로 지운 경우. 해시만 보고 건너뛰면 대여소 없이 뜬다.
		jdbc.sql("delete from ref.stations").update();

		var result = loader.loadIfChanged(REGIONS, STATIONS);

		assertThat(result).isNotNull();
		assertThat(count("select count(*) from ref.stations where active")).isEqualTo(2716);
	}

	private String stationsSha() {
		return jdbc.sql("select sha256 from ref.source_files where file_name = :f")
			.param("f", RefDataLoader.STATIONS_FILE)
			.query(String.class)
			.single();
	}

	private int count(String sql) {
		return jdbc.sql(sql).query(Integer.class).single();
	}

	private Map<String, Object> station(String stationId) {
		return jdbc.sql("select station_no, name, region_id, active from ref.stations where station_id = :id")
			.param("id", stationId)
			.query()
			.singleRow();
	}
}
