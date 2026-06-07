package com.syndicate.qe.bdd.runner;

import org.junit.platform.suite.api.ConfigurationParameter;
import org.junit.platform.suite.api.IncludeEngines;
import org.junit.platform.suite.api.SelectClasspathResource;
import org.junit.platform.suite.api.Suite;

import static io.cucumber.junit.platform.engine.Constants.GLUE_PROPERTY_NAME;
import static io.cucumber.junit.platform.engine.Constants.PLUGIN_PROPERTY_NAME;

/**
 * JUnit 5 launcher for the Cucumber BDD pack.
 *
 * Discovers every *.feature file under src/test/resources/feature and binds the
 * step definitions in com.syndicate.qe.bdd.steps. Override the suite from the
 * command line with -Dcucumber.filter.tags="@Functional" etc.
 */
@Suite
@IncludeEngines("cucumber")
@SelectClasspathResource("feature")
@ConfigurationParameter(key = GLUE_PROPERTY_NAME, value = "com.syndicate.qe.bdd.steps")
@ConfigurationParameter(key = PLUGIN_PROPERTY_NAME, value = "pretty,summary,html:target/cucumber-html-report,json:target/cucumber.json,junit:target/cucumber-junit.xml")
public class BddRunnerTest {
    // No code needed — discovery & execution handled by the Cucumber engine.
}
