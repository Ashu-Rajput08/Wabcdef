import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  ArrowDownRight,
  ArrowUpRight,
  CalendarDays,
  Download,
  Percent,
  Wind,
  Zap,
} from "lucide-react";

const API = import.meta.env.VITE_API_URL || "";

const formatNumber = (value, digits = 0) =>
  value == null
    ? "—"
    : new Intl.NumberFormat("en-GB", {
        maximumFractionDigits: digits,
      }).format(value);

const formatTime = (isoTime) =>
  new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Europe/London",
  }).format(new Date(isoTime));

const average = (points, key) =>
  points.length
    ? points.reduce((sum, point) => sum + point[key], 0) / points.length
    : null;

function Chart({ points }) {
  if (!points.length) {
    return (
      <div className="empty">
        No paired actual and forecast observations for this selection.
      </div>
    );
  }

  const width = 1000;
  const height = 270;
  const padding = { left: 52, right: 18, top: 16, bottom: 32 };
  const maxValue = Math.max(
    ...points.flatMap((point) => [point.forecast, point.actual]),
  ) * 1.08 || 1;
  const x = (index) =>
    padding.left +
    (index / Math.max(points.length - 1, 1)) *
      (width - padding.left - padding.right);
  const y = (value) =>
    padding.top +
    (1 - value / maxValue) * (height - padding.top - padding.bottom);
  const linePath = (key) =>
    points
      .map(
        (point, index) =>
          (index ? "L" : "M") +
          x(index).toFixed(1) +
          "," +
          y(point[key]).toFixed(1),
      )
      .join(" ");
  const ticks = [0, 0.25, 0.5, 0.75, 1].map(
    (fraction) => maxValue * fraction,
  );
  const labelIndexes = [
    0,
    Math.floor((points.length - 1) / 2),
    points.length - 1,
  ];

  return (
    <div className="chart-wrap">
      <svg
        viewBox={"0 0 " + width + " " + height}
        role="img"
        aria-label="Hourly actual and day-ahead forecast wind generation"
      >
        {ticks.map((value, index) => (
          <g key={"tick-" + index}>
            <line
              x1={padding.left}
              y1={y(value)}
              x2={width - padding.right}
              y2={y(value)}
              className="gridline"
            />
            <text
              x={padding.left - 10}
              y={y(value) + 4}
              textAnchor="end"
              className="axis-label"
            >
              {Math.round(value / 1000)}k
            </text>
          </g>
        ))}
        <path d={linePath("forecast")} className="line forecast-line" />
        <path d={linePath("actual")} className="line actual-line" />
        {points.map((point, index) => (
          <g key={point.startTime}>
            <circle
              cx={x(index)}
              cy={y(point.forecast)}
              r="3"
              className="chart-point forecast-point"
            >
              <title>
                {formatTime(point.startTime)} forecast: {formatNumber(point.forecast)} MW
              </title>
            </circle>
            <circle
              cx={x(index)}
              cy={y(point.actual)}
              r="3"
              className="chart-point actual-point"
            >
              <title>
                {formatTime(point.startTime)} actual: {formatNumber(point.actual)} MW
              </title>
            </circle>
          </g>
        ))}
        {labelIndexes.map((index, labelIndex) => (
          <text
            key={"label-" + labelIndex}
            x={x(index)}
            y={height - 8}
            textAnchor={
              labelIndex === 0
                ? "start"
                : labelIndex === 2
                  ? "end"
                  : "middle"
            }
            className="axis-label"
          >
            {points.length > 24
              ? new Date(points[index].localTime).toLocaleDateString("en-GB", {
                  day: "2-digit",
                  month: "short",
                  timeZone: "Europe/London",
                })
              : formatTime(points[index].startTime)}
          </text>
        ))}
      </svg>
    </div>
  );
}

function App() {
  const [cutoff, setCutoff] = useState("11:00");
  const [day, setDay] = useState("all");
  const [payload, setPayload] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(true);

  useEffect(() => {
    let active = true;
    setBusy(true);
    setError("");
    setPayload(null);

    const dayQuery = day === "all" ? "" : "&day=" + day;
    fetch(API + "/api/analysis?cutoff=" + cutoff + dayQuery)
      .then((response) => {
        if (!response.ok) {
          throw new Error("API returned " + response.status);
        }
        return response.json();
      })
      .then((data) => {
        if (active) setPayload(data);
      })
      .catch((fetchError) => {
        if (active) setError(fetchError.message);
      })
      .finally(() => {
        if (active) setBusy(false);
      });

    return () => {
      active = false;
    };
  }, [cutoff, day]);

  const points = payload?.points || [];
  const chartPoints = useMemo(
    () =>
      points.filter(
        (_, index) =>
          index === 0 ||
          index === points.length - 1 ||
          points.length <= 72 ||
          index % Math.ceil(points.length / 72) === 0,
      ),
    [points],
  );
  const metrics = payload?.metrics || {};
  const meanActual = average(points, "actual");
  const meanForecast = average(points, "forecast");

  const downloadCsv = () => {
    const rows = [
      [
        "delivery_time_london",
        "forecast_mw",
        "actual_mw",
        "error_mw",
        "forecast_published_utc",
      ],
      ...points.map((point) => [
        point.localTime,
        point.forecast,
        point.actual,
        point.error,
        point.publishTime,
      ]),
    ];
    const csvContent = rows
      .map((row) =>
        row
          .map((value) => '"' + String(value).replaceAll('"', '""') + '"')
          .join(","),
      )
      .join("\n");
    const blob = new Blob([csvContent], { type: "text/csv" });
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement("a");
    const dayName = day === "all" ? "all-days" : day;

    link.href = objectUrl;
    link.download =
      "wind-desk-jan-2026-" +
      dayName +
      "-cutoff-" +
      cutoff.replace(":", "") +
      ".csv";
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
  };

  const days = [...new Set(points.map((point) => point.date))];

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">
            <Wind size={19} />
          </span>
          <span>
            Wind Desk<span className="brand-dot">.</span>
          </span>
        </div>

        <div className="workspace">
          <div className="workspace-icon">G</div>
          <div>
            <b>GB Energy Utility</b>
            <small>Trading &amp; analytics</small>
          </div>
        </div>

        <div className="nav-label">WORKSPACE</div>
        <nav>
          <div className="nav-item active" aria-current="page">
            <Activity size={17} />
            Wind intelligence
          </div>
        </nav>

        <div className="sidebar-bottom">
          <div className="live-pill">
            <i />
            Historical pilot data
          </div>
          <div className="profile">
            <div className="avatar">TR</div>
            <div>
              <b>Trading desk</b>
              <small>GB power</small>
            </div>
          </div>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <div className="breadcrumb">
            Portfolio <span>/</span> Renewables <span>/</span>{" "}
            <b>Wind intelligence</b>
          </div>
          <div className="top-meta">
            <span className="status">
              <i />
              Elexon snapshot
            </span>
            <span className="divider" />
            <span>GB · Europe/London</span>
          </div>
        </header>

        <div className="content">
          <section className="intro">
            <div>
              <div className="eyebrow">
                <span className="eyebrow-dot" />
                HISTORICAL FORECAST ACCURACY
              </div>
              <h1>Wind intelligence</h1>
              <p>
                Compare the day-ahead forecast with recorded wind generation.
              </p>
            </div>
            <button
              className="export-btn"
              onClick={downloadCsv}
              disabled={!points.length}
            >
              <Download size={16} />
              Export data
            </button>
          </section>

          <section className="filter-row">
            <div className="period">
              <CalendarDays size={16} />
              <b>January 2026 decision dates</b>
              <span className="period-tag">DELIVERY: 2 JAN–1 FEB</span>
            </div>
            <div className="filters">
              <label>
                Delivery day
                <select value={day} onChange={(event) => setDay(event.target.value)}>
                  <option value="all">All days</option>
                  {days.map((deliveryDay) => (
                    <option key={deliveryDay} value={deliveryDay}>
                      {new Date(deliveryDay + "T12:00:00").toLocaleDateString(
                        "en-GB",
                        {
                          day: "2-digit",
                          month: "short",
                          timeZone: "Europe/London",
                        },
                      )}
                    </option>
                  ))}
                </select>
              </label>
              <label
                title={
                  "The time on the day before delivery up to which a forecast " +
                  "must have been published to count. The assignment does not " +
                  "prescribe a time."
                }
              >
                Cutoff on previous day
                <input
                  type="time"
                  step="1800"
                  value={cutoff}
                  onChange={(event) => {
                    if (event.target.value) setCutoff(event.target.value);
                  }}
                  aria-label="Assumed day-ahead forecast cutoff in Europe/London time"
                />
              </label>
            </div>
          </section>

          {error && (
            <div className="error">
              Could not load analysis: {error}. Start the FastAPI backend and
              check <code>/api/health</code>.
            </div>
          )}

          <section className="metrics-grid">
            <article className="metric-card">
              <div className="metric-top">
                <span>Mean absolute error</span>
                <span className="metric-icon orange">
                  <Activity size={17} />
                </span>
              </div>
              <div className="metric-value">
                {busy ? "…" : formatNumber(metrics.maeMw)}
                <small> MW</small>
              </div>
              <div className="metric-foot">
                <span className="neutral-dot" />
                Average miss per hour
              </div>
            </article>

            <article className="metric-card">
              <div className="metric-top">
                <span>Forecast bias</span>
                <span className="metric-icon green">
                  {metrics.biasMw < 0 ? (
                    <ArrowDownRight size={17} />
                  ) : (
                    <ArrowUpRight size={17} />
                  )}
                </span>
              </div>
              <div className="metric-value">
                {busy ? "…" : formatNumber(metrics.biasMw)}
                <small> MW</small>
              </div>
              <div className="metric-foot">
                {metrics.biasMw == null
                  ? "No matched observations"
                  : metrics.biasMw > 0
                    ? "Over-forecast on average"
                    : "Under-forecast on average"}
              </div>
            </article>

            <article className="metric-card">
              <div className="metric-top">
                <span>WAPE</span>
                <span className="metric-icon blue">
                  <Percent size={17} />
                </span>
              </div>
              <div className="metric-value">
                {busy ? "…" : formatNumber(metrics.wapePct, 1)}
                <small>%</small>
              </div>
              <div className="metric-foot">
                Absolute error ÷ actual generation
              </div>
            </article>

            <article className="metric-card">
              <div className="metric-top">
                <span>Root mean square error</span>
                <span className="metric-icon violet">
                  <Zap size={17} />
                </span>
              </div>
              <div className="metric-value">
                {busy ? "…" : formatNumber(metrics.rmseMw)}
                <small> MW</small>
              </div>
              <div className="metric-foot">
                Larger misses receive more weight
              </div>
            </article>
          </section>

          <section className="panel chart-panel">
            <div className="panel-head">
              <div>
                <h2>Forecast vs actual</h2>
                <p>
                  Hourly wind generation · MW · {payload?.count ?? "…"} matched
                  hours
                </p>
              </div>
              <div className="legend">
                <span>
                  <i className="legend-forecast" />
                  Day-ahead forecast
                </span>
                <span>
                  <i className="legend-actual" />
                  Actual generation
                </span>
              </div>
            </div>

            {busy ? (
              <div className="loading">Loading saved Elexon snapshot…</div>
            ) : (
              <Chart points={chartPoints} />
            )}

            <div className="chart-note">
              <span className="note-mark">i</span>
              <span>
                For each delivery hour, the chart uses the latest WINDFOR
                forecast published by {cutoff} Europe/London on the previous
                day. The assignment does not prescribe this decision time, so
                it is an adjustable assumption. Each hourly actual is the
                average of two half-hour FUELHH readings. This is a historical
                snapshot, not a live feed or purchase recommendation.
              </span>
            </div>
          </section>

          <section className="lower-grid">
            <article className="panel summary-panel">
              <div className="panel-head">
                <div>
                  <h2>Decision snapshot</h2>
                  <p>Selected delivery window</p>
                </div>
                <span className="tag">DAY-AHEAD</span>
              </div>
              <div className="snapshot-row">
                <span>Forecast wind generation</span>
                <b>
                  {formatNumber(meanForecast)} <small>MW avg</small>
                </b>
              </div>
              <div className="snapshot-row">
                <span>Actual wind generation</span>
                <b>
                  {formatNumber(meanActual)} <small>MW avg</small>
                </b>
              </div>
              <div className="snapshot-row">
                <span>Forecast versions in source history</span>
                <b>{formatNumber(payload?.source?.forecastRecords)}</b>
              </div>
              <div className="summary-callout">
                <Wind size={17} />
                <span>
                  Wind is variable; assess forecast error alongside demand and
                  other portfolio information before making a procurement
                  decision.
                </span>
              </div>
            </article>

            <article className="panel methodology">
              <div className="panel-head">
                <div>
                  <h2>How to read this</h2>
                  <p>Methodology at a glance</p>
                </div>
              </div>
              <div className="method-row">
                <span className="step">01</span>
                <div>
                  <b>Latest available forecast</b>
                  <p>
                    Only forecasts published by the chosen prior-day cutoff are
                    eligible.
                  </p>
                </div>
              </div>
              <div className="method-row">
                <span className="step">02</span>
                <div>
                  <b>Comparable resolution</b>
                  <p>
                    Two 30-minute FUELHH readings are averaged to match hourly
                    WINDFOR.
                  </p>
                </div>
              </div>
              <div className="method-row">
                <span className="step">03</span>
                <div>
                  <b>Transparent error measures</b>
                  <p>
                    MAE shows typical miss; RMSE weights large misses; bias
                    shows direction; WAPE scales error to outturn.
                  </p>
                </div>
              </div>
            </article>
          </section>

          <footer>
            <span>Source: Elexon BMRS · FUELHH + WINDFOR</span>
            <span>
              Wind Desk <b>·</b> pilot 1.0
            </span>
          </footer>
        </div>
      </main>
    </div>
  );
}

export default App;

