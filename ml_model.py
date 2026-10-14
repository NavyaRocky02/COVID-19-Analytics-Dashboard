
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

BASE_DIR = Path(__file__).parent
DATASET_PATH = BASE_DIR / "data" / "covid19.csv"

TEST_WEEKS = 43


def make_features(weekly, max_lag):
    data = weekly.copy()

    for lag in range(1, max_lag + 1):
        data[f"cases_lag_{lag}"] = data["cases"].shift(lag)

    previous = data["cases"].shift(1)

    data["previous_4_week_average"] = previous.rolling(4).mean()
    data["previous_4_week_max"] = previous.rolling(4).max()

    if max_lag >= 8:
        data["previous_8_week_average"] = previous.rolling(8).mean()
        data["previous_8_week_max"] = previous.rolling(8).max()

    features = [
        *[f"cases_lag_{lag}" for lag in range(1, max_lag + 1)],
        "previous_4_week_average",
        "previous_4_week_max",
    ]

    if max_lag >= 8:
        features += [
            "previous_8_week_average",
            "previous_8_week_max",
        ]

    return data.dropna(subset=features), features


def evaluate(name, actual, predicted):
    mae = mean_absolute_error(actual, predicted)
    rmse = np.sqrt(mean_squared_error(actual, predicted))
    r2 = r2_score(actual, predicted)

    print(
        f"{name:<28} "
        f"MAE={mae:8.2f}  RMSE={rmse:8.2f}  R2={r2:7.3f}"
    )

    return mae, rmse, r2


def main():
    if not DATASET_PATH.exists():
        raise FileNotFoundError(
            f"Dataset not found: {DATASET_PATH}"
        )

    df = pd.read_csv(DATASET_PATH)

    required_columns = ["Week Start", "Cases - Weekly"]
    missing_columns = [
        column for column in required_columns
        if column not in df.columns
    ]

    if missing_columns:
        raise ValueError(
            f"Dataset is missing columns: {missing_columns}"
        )

    df["Week Start"] = pd.to_datetime(
        df["Week Start"], errors="coerce"
    )
    df["Cases - Weekly"] = pd.to_numeric(
        df["Cases - Weekly"], errors="coerce"
    )

    df = df.dropna(subset=["Week Start", "Cases - Weekly"])

    weekly = (
        df.groupby("Week Start")["Cases - Weekly"]
        .sum()
        .sort_index()
        .resample("W-MON")
        .sum()
        .to_frame("cases")
    )

    # Create feature sets for both Random Forest models.
    data4, features4 = make_features(weekly, 4)
    data8, features8 = make_features(weekly, 8)

    # Compare both models on exactly the same target weeks.
    common_index = data8.index
    data4 = data4.loc[common_index]

    if len(common_index) <= TEST_WEEKS + 10:
        raise ValueError("Not enough usable weekly data.")

    split = len(common_index) - TEST_WEEKS
    train_index = common_index[:split]
    test_index = common_index[split:]

    print("\nFair model comparison")
    print("---------------------")
    print(f"Training weeks: {len(train_index)}")
    print(f"Testing weeks:  {len(test_index)}")
    print(
        f"Test window: {test_index.min().date()} "
        f"to {test_index.max().date()}"
    )
    print(
        "\nMetrics (lower MAE/RMSE is better; "
        "higher R2 is better)"
    )

    results = []

    # Evaluate both Random Forest configurations.
    for name, data, features in [
        ("Random Forest (4-week)", data4, features4),
        ("Random Forest (8-week)", data8, features8),
    ]:
        model = RandomForestRegressor(
            n_estimators=200,
            max_depth=8,
            min_samples_leaf=2,
            random_state=42,
            n_jobs=-1,
        )

        model.fit(
            data.loc[train_index, features],
            data.loc[train_index, "cases"],
        )

        predictions = model.predict(
            data.loc[test_index, features]
        )

        metrics = evaluate(
            name,
            data.loc[test_index, "cases"],
            predictions,
        )

        results.append((name, metrics[0]))

    # Compare simple forecasting methods on the same test weeks.
    actual = weekly.loc[test_index, "cases"]

    # Method 1: Previous week's actual cases.
    previous_week = weekly["cases"].shift(1).loc[test_index]

    # Method 2: Average of the previous four weeks.
    moving_average = (
        weekly["cases"]
        .shift(1)
        .rolling(4)
        .mean()
        .loc[test_index]
    )

    # Method 3: Cases from 52 weeks earlier.
    seasonal_baseline = (
        weekly["cases"]
        .shift(52)
        .loc[test_index]
    )

    print("\nBaseline comparison")
    print("-------------------")

    baselines = [
        ("Last-week baseline", previous_week),
        ("4-week moving average", moving_average),
        ("52-week seasonal baseline", seasonal_baseline),
    ]

    for name, predictions in baselines:
        # Exclude dates without enough historical observations.
        valid = actual.notna() & predictions.notna()

        metrics = evaluate(
            name,
            actual.loc[valid],
            predictions.loc[valid],
        )

        results.append((name, metrics[0]))

    print("\nLowest test MAE:")
    print(f"{min(results, key=lambda item: item[1])}")


if __name__ == "__main__":
    main()
