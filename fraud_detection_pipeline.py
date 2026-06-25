import os
import pickle

import matplotlib
matplotlib.use("Agg")  # headless rendering, no display needed
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from imblearn.over_sampling import SMOTE
from sklearn.datasets import make_classification
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import train_test_split

RANDOM_STATE = 42
DATA_PATH = "creditcard.csv"
MODEL_PATH = "fraud_model.pkl"
STREAM_DATA_PATH = "stream_data.pkl"
CLASS_DIST_PLOT = "class_distribution.png"
CONFUSION_MATRIX_PLOT = "confusion_matrix.png"

FEATURE_COLUMNS = ["Time"] + [f"V{i}" for i in range(1, 29)] + ["Amount"]
TARGET_COLUMN = "Class"

sns.set_style("darkgrid")


def load_or_generate_dataset(n_samples: int = 150_000, fraud_ratio: float = 0.0025) -> pd.DataFrame:
    """Load the real Kaggle dataset if present, otherwise synthesize one
    that mirrors its schema and class imbalance."""
    if os.path.exists(DATA_PATH):
        print(f"[pipeline] Found {DATA_PATH} -- loading real Kaggle data.")
        return pd.read_csv(DATA_PATH)

    print(
        f"[pipeline] {DATA_PATH} not found. Generating a synthetic "
        f"{n_samples:,} row dataset with the same schema "
        f"(Time, V1-V28, Amount, Class) and a {fraud_ratio:.2%} fraud rate."
    )
    print("[pipeline] Drop a real creditcard.csv in the project root to train on actual data instead.")

    rng = np.random.RandomState(RANDOM_STATE)


    X, y = make_classification(
        n_samples=n_samples,
        n_features=28,
        n_informative=18,
        n_redundant=6,
        n_repeated=0,
        n_clusters_per_class=1,
        weights=[1 - fraud_ratio, fraud_ratio],
        flip_y=0.0005,
        class_sep=2.8,
        random_state=RANDOM_STATE,
    )
    df = pd.DataFrame(X, columns=[f"V{i}" for i in range(1, 29)])
    df[TARGET_COLUMN] = y

    df["Time"] = np.sort(rng.uniform(0, 172_800, size=n_samples)).astype(int)


    legit_amount = rng.lognormal(mean=3.0, sigma=1.3, size=n_samples)
    fraud_amount = np.where(
        rng.rand(n_samples) < 0.7,
        rng.lognormal(mean=1.5, sigma=1.0, size=n_samples),   # small test charges
        rng.lognormal(mean=5.5, sigma=0.8, size=n_samples),   # occasional large hit
    )
    df["Amount"] = np.where(df[TARGET_COLUMN] == 1, fraud_amount, legit_amount).round(2)
    df["Amount"] = df["Amount"].clip(0.5, 25_000)

    return df[["Time"] + [f"V{i}" for i in range(1, 29)] + ["Amount", TARGET_COLUMN]]


def plot_class_distribution(df: pd.DataFrame) -> None:
    counts = df[TARGET_COLUMN].value_counts().sort_index()
    fig, ax = plt.subplots(figsize=(6, 4.5))
    sns.barplot(x=["Normal", "Fraud"], y=counts.values, palette=["#3fb98c", "#e2495c"], ax=ax)
    ax.set_title("Class Distribution (Normal vs Fraud)")
    ax.set_ylabel("Transaction count")
    for i, v in enumerate(counts.values):
        ax.text(i, v, f"{v:,}\n({v / counts.sum():.2%})", ha="center", va="bottom")
    fig.tight_layout()
    fig.savefig(CLASS_DIST_PLOT, dpi=150)
    plt.close(fig)
    print(f"[pipeline] Saved {CLASS_DIST_PLOT}")


def plot_confusion_matrix(y_true, y_pred) -> None:
    cm = confusion_matrix(y_true, y_pred)
    fig, ax = plt.subplots(figsize=(5, 4.5))
    sns.heatmap(
        cm, annot=True, fmt="d", cmap="rocket_r",
        xticklabels=["Normal", "Fraud"], yticklabels=["Normal", "Fraud"], ax=ax,
    )
    ax.set_xlabel("Predicted")
    ax.set_ylabel("Actual")
    ax.set_title("Confusion Matrix")
    fig.tight_layout()
    fig.savefig(CONFUSION_MATRIX_PLOT, dpi=150)
    plt.close(fig)
    print(f"[pipeline] Saved {CONFUSION_MATRIX_PLOT}")


def build_stream_pool(X_test: pd.DataFrame, y_test: pd.Series, fraud_weight: float = 0.12, pool_size: int = 2000) -> pd.DataFrame:
  
    fraud_idx = y_test[y_test == 1].index
    normal_idx = y_test[y_test == 0].index

    n_fraud = min(len(fraud_idx), max(1, int(pool_size * fraud_weight)))
    n_normal = pool_size - n_fraud

    rng = np.random.RandomState(RANDOM_STATE)
    chosen_fraud = rng.choice(fraud_idx, size=n_fraud, replace=len(fraud_idx) < n_fraud)
    chosen_normal = rng.choice(normal_idx, size=n_normal, replace=len(normal_idx) < n_normal)

    chosen = np.concatenate([chosen_fraud, chosen_normal])
    rng.shuffle(chosen)

    pool = X_test.loc[chosen].copy()
    pool[TARGET_COLUMN] = y_test.loc[chosen].values
    return pool.reset_index(drop=True)


def main():
    df = load_or_generate_dataset()
    print(f"[pipeline] Dataset shape: {df.shape}")

    plot_class_distribution(df)

    X = df[FEATURE_COLUMNS]
    y = df[TARGET_COLUMN]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.25, stratify=y, random_state=RANDOM_STATE
    )

    print("[pipeline] Balancing training data with SMOTE...")
    smote = SMOTE(random_state=RANDOM_STATE)
    X_train_bal, y_train_bal = smote.fit_resample(X_train, y_train)
    print(f"[pipeline] Training set after SMOTE: {X_train_bal.shape}, "
          f"fraud share = {y_train_bal.mean():.2%}")

    print("[pipeline] Training RandomForestClassifier...")
    model = RandomForestClassifier(
        n_estimators=200,
        max_depth=12,
        n_jobs=-1,
        class_weight=None,  # SMOTE already balanced the classes
        random_state=RANDOM_STATE,
    )
    model.fit(X_train_bal, y_train_bal)

    y_pred = model.predict(X_test)
    print("[pipeline] Evaluation on held-out test set:")
    print(classification_report(y_test, y_pred, target_names=["Normal", "Fraud"]))

    plot_confusion_matrix(y_test, y_pred)

    with open(MODEL_PATH, "wb") as f:
        pickle.dump({"model": model, "feature_columns": FEATURE_COLUMNS}, f)
    print(f"[pipeline] Saved trained model to {MODEL_PATH}")

    stream_pool = build_stream_pool(X_test, y_test)
    with open(STREAM_DATA_PATH, "wb") as f:
        pickle.dump(stream_pool, f)
    print(f"[pipeline] Saved {len(stream_pool)}-row live stream pool to {STREAM_DATA_PATH} "
          f"(fraud share in pool: {stream_pool[TARGET_COLUMN].mean():.2%})")

    print("[pipeline] Done. Start the API with: uvicorn app:app --reload")


if __name__ == "__main__":
    main()
