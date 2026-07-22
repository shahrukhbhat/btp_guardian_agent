/**
 * Jest reporter that emits test_report.json at the project root.
 *
 * Schema mirrors the agent-side report written by sap-agent-bootstrap's
 * conftest.py so downstream tooling (evaluation scorers, dashboards) can
 * consume both shapes uniformly.
 *
 * Coverage is read from coverage/coverage-final.json (written by Jest when
 * --coverage is on) rather than aggregatedResults.coverageMap, which is not
 * reliably populated for custom reporters.
 */

const { existsSync, readFileSync, writeFileSync } = require("node:fs");
const { join } = require("node:path");

class TestReportReporter {
  constructor(globalConfig, options = {}) {
    this._rootDir = globalConfig.rootDir;
    this._coverageDirectory = globalConfig.coverageDirectory || join(this._rootDir, "coverage");
    this._outputPath = options.outputPath
      ? join(this._rootDir, options.outputPath)
      : join(this._rootDir, "test_report.json");
    this._sectionName = options.sectionName || "CAP Tests";
    this._sectionMarker = options.sectionMarker || "cap_tests";
  }

  onRunComplete(_contexts, results) {
    const tests = [];
    for (const file of results.testResults) {
      for (const t of file.testResults) {
        tests.push({
          name: t.fullName || t.title,
          outcome: t.status === "passed" ? "passed" : t.status === "failed" ? "failed" : "skipped",
          duration: round((t.duration || 0) / 1000, 4),
        });
      }
    }

    const total = results.numTotalTests;
    const passed = results.numPassedTests;
    const failed = results.numFailedTests;
    const skipped = results.numPendingTests + results.numTodoTests;
    const score = total ? round((passed / total) * 100, 2) : 0.0;

    const section = {
      name: this._sectionName,
      marker: this._sectionMarker,
      total,
      passed,
      failed,
      skipped,
      score,
      tests,
    };

    const summary = { total, passed, failed, score };
    const coverage = readCoverage(this._coverageDirectory);
    if (coverage !== null) summary.coverage = coverage;

    const report = {
      summary,
      sections: [section],
    };

    writeFileSync(this._outputPath, `${JSON.stringify(report, null, 2)}\n`);
    console.log(`\nReport written to ${this._outputPath}`);
  }
}

function readCoverage(coverageDir) {
  const finalPath = join(coverageDir, "coverage-final.json");
  if (!existsSync(finalPath)) return null;
  let data;
  try {
    data = JSON.parse(readFileSync(finalPath, "utf8"));
  } catch {
    return null;
  }

  let total = 0;
  let covered = 0;
  for (const file of Object.values(data)) {
    const s = file?.s ?? {};
    for (const v of Object.values(s)) {
      total += 1;
      if (v > 0) covered += 1;
    }
  }
  if (!total) return null;
  return round((covered / total) * 100, 2);
}

function round(n, digits) {
  const f = 10 ** digits;
  return Math.round(n * f) / f;
}

module.exports = TestReportReporter;
