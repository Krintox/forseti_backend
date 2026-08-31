# Statistical Fidelity & Realism Validation Methodology

## Defensible Positioning
FORSETI includes a reproducible fidelity harness that compares the synthetic transaction generator against two independent public real-world transaction distributions (the PaySim Mobile Money dataset and the ULB Credit Card dataset) whenever the licensed CSVs are supplied locally - see `scripts/download_anchors.py`. PaySim and ULB are treated as distinct, independent realism anchors rather than merged into a single distribution.

**Current status:** neither anchor dataset is present in this repository (`data/anchors/`), so `artifacts/fidelity/fidelity_report.json` correctly reports `NOT RUN / DATASET UNAVAILABLE` and no realism or calibration claim is made. Only say "FORSETI was statistically validated against PaySim/ULB" once the corresponding experiment has actually executed and produced that artifact - never because the harness exists.

## Fidelity Battery
1. **Two-Sample Kolmogorov-Smirnov Test (`scipy.stats.ks_2samp`)**: Evaluates the cumulative distribution function alignment of continuous transaction amount distributions.
2. **Categorical Jensen-Shannon Divergence (`scipy.spatial.distance.jensenshannon`)**: Evaluates probability mass differences across merchant categories and temporal diurnal patterns.
3. **Correlation Matrix Distance (Frobenius Norm)**: Evaluates pairwise feature covariance divergence between synthetic and empirical datasets.
4. **Synthetic Discriminator Classifier AUC**: Trains a binary classifier (Real = 0, Synthetic = 1). A target ROC-AUC near 0.50 demonstrates indistinguishable statistical properties.
5. **Train-on-Synthetic, Test-on-Real (TSTR)**: Evaluates model transferability by comparing PR-AUC when trained on synthetic vs real data and tested on the real anchor test split.
