package com.seoulbike.backend.system;

import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.ViewControllerRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

/** Spring은 루트에서만 index.html을 자동으로 보여준다. /swagger도 계약 화면을 열게 한다. */
@Configuration
class DocsViewConfig implements WebMvcConfigurer {

	@Override
	public void addViewControllers(ViewControllerRegistry registry) {
		registry.addViewController("/swagger").setViewName("forward:/swagger/index.html");
		registry.addViewController("/swagger/").setViewName("forward:/swagger/index.html");
	}
}
