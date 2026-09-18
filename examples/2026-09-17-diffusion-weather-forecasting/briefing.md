# diffusion models for weather forecasting

## Executive summary

Diffusion models unify diverse weather tasks, improving ensemble forecasts and enabling scalable climate emulation.

## Background

Weather and climate modeling is fragmented across specialized models for distinct tasks, limiting efficiency and scalability [1]. Diffusion models offer a unified framework for probabilistic forecasting, downscaling, and reconstruction [1]. Recent advances in latent diffusion improve high-resolution ensemble forecasts [2], [3] and scalable climate emulation [5]. However, the field lacks clarity on the fundamental drivers of forecast accuracy [4].

## Method

This briefing synthesizes five papers from a targeted search on diffusion models for weather forecasting. Inclusion criteria: papers directly applying diffusion models to atmospheric or climate modeling, published 2025-2026. Exclusion criteria: papers not using diffusion models or not focused on weather/climate. The papers were analyzed for problem, method, data, and key findings.

### Queries

- `arxiv: all:"diffusion model" AND all:"weather forecasting"`
- `arxiv: all:"diffusion model" AND all:"precipitation nowcasting"`
- `arxiv: all:"diffusion model" AND all:"climate modeling"`
- `arxiv: all:"denoising diffusion" AND all:"weather"`
- `arxiv: all:"score-based generative model" AND all:"meteorology"`

### Inclusion criteria

- The paper proposes or applies a diffusion model for weather forecasting or related meteorological tasks.
- The paper is a primary research article, not a review or survey.
- The paper is available on arXiv.
- The paper is written in English.

### Exclusion criteria

- The paper does not use diffusion models.
- The paper is not related to weather, climate, or meteorology.
- The paper is a review, survey, or opinion piece.
- The paper is not in English.

## Paper by paper

### [1] WIND: Weather Inverse Diffusion for Zero-Shot Atmospheric Modeling

- Authors: Michael Aich, Andreas Fürst, Florian Sestak, Carlos Ruiz-Gonzalez, Niklas Boers, Johannes Brandstetter
- Year: 2026
- Venue: arxiv
- Link: https://arxiv.org/abs/2602.03924v2

**Problem.** The paper addresses the fragmentation in atmospheric modeling where specialized models are trained individually for distinct tasks. It aims to unify this landscape with a single pre-trained foundation model that can handle diverse weather and climate problems without task-specific fine-tuning.

**Method.** The authors introduce WIND, a pre-trained foundation model that uses a self-supervised video reconstruction objective with an unconditional video diffusion model to learn a task-agnostic prior of the atmosphere. At inference, diverse problems are framed as inverse problems and solved via posterior sampling.

**Data and experiments.** The abstract does not specify datasets or benchmarks. It mentions tasks including probabilistic forecasting, spatial and temporal downscaling, reconstruction from sparse observations, enforcing global dry air mass conservation, and exploring extreme weather events under out-of-distribution thermodynamic perturbations.

**Key findings**

- WIND can replace specialized baselines across a vast array of tasks without any task-specific fine-tuning.
- The model handles probabilistic forecasting, spatial and temporal downscaling, reconstruction from sparse observations, and enforcing global dry air mass conservation.
- WIND can be applied to explore extreme weather events under prescribed out-of-distribution thermodynamic perturbations.
- The approach offers a computationally efficient alternative for AI-based atmospheric modeling.

**Limitations**

- not stated in the abstract

**Reusable ideas**

- Using a self-supervised video reconstruction objective with an unconditional video diffusion model to learn a task-agnostic prior.
- Framing diverse domain-specific problems as inverse problems and solving them via posterior sampling.
- Applying a single pre-trained foundation model to multiple tasks without fine-tuning.

### [2] PuYun-LDM: A Latent Diffusion Model for High-Resolution Ensemble Weather Forecasts

- Authors: Lianjun Wu, Shengchen Zhu, Yuxuan Liu, Liuyu Kai, Xiaoduan Feng, Duomin Wang, Wenshuo Liu, Jingxuan Zhang, Kelvin Li, Bin Wang
- Year: 2026
- Venue: arxiv
- Link: https://arxiv.org/abs/2602.11807v2

**Problem.** Latent diffusion models (LDMs) have limited diffusability in high-resolution (<=0.25°) ensemble weather forecasting. Existing approaches either rely on task-agnostic foundation models or impose identical spectral regularization across channels, which fails due to inter-variable spectral heterogeneity in multivariate meteorological data.

**Method.** The authors propose a 3D Masked AutoEncoder (3D-MAE) that encodes weather-state evolution features as additional conditioning for the diffusion model. They also introduce a Variable-Aware Masked Frequency Modeling (VA-MFM) strategy that adaptively selects thresholds based on the spectral energy distribution of each variable. Together, these form PuYun-LDM.

**Data and experiments.** The paper evaluates PuYun-LDM on high-resolution (<=0.25°) ensemble weather forecasting. It generates a 15-day global forecast with a 6-hour temporal resolution. The model is compared to ENS (ECMWF ensemble) at short and longer lead times. Experiments are run on a single NVIDIA H200 GPU.

**Key findings**

- PuYun-LDM enhances latent diffusability compared to existing LDMs.
- PuYun-LDM achieves superior performance to ENS at short lead times.
- PuYun-LDM remains comparable to ENS at longer horizons.
- PuYun-LDM generates a 15-day global forecast with 6-hour temporal resolution in five minutes on a single NVIDIA H200 GPU.
- Ensemble forecasts can be efficiently produced in parallel.

**Limitations**

- not stated in the abstract

**Reusable ideas**

- Use a 3D Masked AutoEncoder to encode spatiotemporal evolution features as conditioning for diffusion models.
- Apply variable-aware adaptive thresholding in frequency-domain regularization to handle heterogeneous spectral energy distributions across channels.
- Leverage latent diffusion models for efficient high-resolution ensemble forecasting with parallel generation.

### [3] LaDCast: A Latent Diffusion Model for Medium-Range Ensemble Weather Forecasting

- Authors: Yilin Zhuang, Karthik Duraisamy
- Year: 2025
- Venue: arxiv
- Link: https://arxiv.org/abs/2506.09193v1

**Problem.** Accurate probabilistic weather forecasting requires both high accuracy and efficient uncertainty quantification, which overburdens ensemble numerical weather prediction and recent machine-learning methods.

**Method.** The authors introduce LaDCast, a global latent-diffusion framework that generates hourly ensemble forecasts in a learned latent space. An autoencoder compresses ERA5 fields, and a transformer-based diffusion model produces sequential latent updates with arbitrary hour initialization, incorporating GeoRoPE, dual-stream attention, and sinusoidal temporal embeddings.

**Data and experiments.** The model is trained and evaluated on ERA5 reanalysis data. It is compared against the European Centre for Medium-Range Forecast IFS-ENS, with evaluation of deterministic and probabilistic skill, and tracking of rare extreme events such as cyclones.

**Key findings**

- LaDCast achieves deterministic and probabilistic skill close to that of IFS-ENS without explicit perturbations.
- LaDCast demonstrates superior performance in tracking rare extreme events such as cyclones, capturing their trajectories more accurately than established models.
- Operating in latent space reduces storage and compute by orders of magnitude, demonstrating a practical path toward forecasting at kilometer-scale resolution in real time.

**Limitations**

- not stated in the abstract

**Reusable ideas**

- Use latent diffusion for efficient ensemble forecasting in high-dimensional spatiotemporal data.
- Incorporate geometric rotary position embeddings to account for spherical geometry in global models.
- Employ dual-stream attention for efficient conditioning in sequential generation.
- Use sinusoidal temporal embeddings to capture seasonal patterns in time-series forecasting.

### [4] Demystifying Data-Driven Probabilistic Medium-Range Weather Forecasting

- Authors: Jean Kossaifi, Nikola Kovachki, Morteza Mardani, Daniel Leibovici, Suman Ravuri, Ira Shokar, Edoardo Calvello, Mohammad Shoaib Abbas, Peter Harrington, Ashay Subramaniam, Noah Brenowitz, Boris Bonev, Wonmin Byeon, Karsten Kreis, Dale Durran, Arash Vahdat, Mike Pritchard, Jan Kautz
- Year: 2026
- Venue: arxiv
- Link: https://arxiv.org/abs/2601.18111v1

**Problem.** The recent revolution in data-driven methods for weather forecasting has led to a fragmented landscape of complex, bespoke architectures and training strategies, obscuring the fundamental drivers of forecast accuracy.

**Method.** The authors introduce a scalable framework for learning multi-scale atmospheric dynamics by combining a directly downsampled latent space with a history-conditioned local projector that resolves high-resolution physics. The framework design is robust to the choice of probabilistic estimator, seamlessly supporting stochastic interpolants, diffusion models, and CRPS-based ensemble training.

**Data and experiments.** Validated against the Integrated Forecasting System and the deep learning probabilistic model GenCast.

**Key findings**

- State-of-the-art probabilistic skill requires neither intricate architectural constraints nor specialized training heuristics.
- The framework achieves statistically significant improvements on most of the variables compared to the Integrated Forecasting System and GenCast.
- Scaling a general-purpose model is sufficient for state-of-the-art medium-range prediction, eliminating the need for tailored training recipes.
- The framework is effective across the full spectrum of probabilistic frameworks.

**Limitations**

- not stated in the abstract

**Reusable ideas**

- Directly downsampled latent space combined with a history-conditioned local projector for multi-scale dynamics.
- Robustness to probabilistic estimator choice (stochastic interpolants, diffusion models, CRPS-based ensemble training).
- Scaling a general-purpose model instead of bespoke architectures for medium-range prediction.

### [5] Field-Space Autoencoder for Scalable Climate Emulators

- Authors: Johannes Meuer, Maximilian Witte, Étiénne Plésiat, Thomas Ludwig, Christopher Kadow
- Year: 2026
- Venue: arxiv
- Link: https://arxiv.org/abs/2601.15102v1

**Problem.** Kilometer-scale Earth system models are essential for capturing local climate change but are computationally expensive and produce petabyte-scale outputs, limiting their utility for applications such as probabilistic risk assessment.

**Method.** The authors present the Field-Space Autoencoder, a scalable climate emulation framework based on a spherical compression model that uses Field-Space Attention to operate on native climate model output, avoiding geometric distortions from forcing spherical data onto Euclidean grids. They train a generative diffusion model on the compressed fields to learn internal variability from low-resolution data and fine-scale physics from high-resolution data.

**Data and experiments.** The abstract does not specify particular datasets or benchmarks. It mentions training on abundant low-resolution data and sparse high-resolution data, and performing zero-shot super-resolution mapping low-resolution large ensembles and scarce high-resolution data into a shared representation.

**Key findings**

- The Field-Space Autoencoder preserves physical structures significantly better than convolutional baselines.
- The model produces a structured compressed field that serves as a good baseline for downstream generative emulation.
- The model can perform zero-shot super-resolution that maps low-resolution large ensembles and scarce high-resolution data into a shared representation.
- The generative diffusion model can simultaneously learn internal variability from abundant low-resolution data and fine-scale physics from sparse high-resolution data.

**Limitations**

- not stated in the abstract

**Reusable ideas**

- Using spherical compression models to avoid geometric distortions when processing data on the sphere.
- Applying attention mechanisms directly on native spherical data for better structure preservation.
- Combining abundant low-resolution data with sparse high-resolution data in a shared latent space for generative modeling.
- Using autoencoder-compressed fields as a basis for downstream generative models.

## Comparison

| Ref | Task | Method | Data | Metrics | Main result |
| --- | --- | --- | --- | --- | --- |
| [1] | Unified atmospheric modeling (forecasting, downscaling, reconstruction) | Unconditional video diffusion with posterior sampling | Not specified | Not specified | Single pre-trained model handles multiple tasks without fine-tuning |
| [2] | High-resolution ensemble weather forecasting | Latent diffusion with 3D-MAE conditioning and VA-MFM | High-resolution (<=0.25°) global data | Comparison to ENS (ECMWF ensemble) | Superior to ENS at short lead times, comparable at longer horizons |
| [3] | Medium-range ensemble weather forecasting | Latent diffusion with autoencoder and transformer | ERA5 reanalysis | Deterministic and probabilistic skill vs IFS-ENS | Skill close to IFS-ENS, better cyclone tracking |
| [4] | Probabilistic medium-range weather forecasting | Downsampled latent space with local projector | Validated against IFS and GenCast | Statistical significance vs IFS and GenCast | General-purpose model achieves state-of-the-art without bespoke design |
| [5] | Climate emulation and super-resolution | Spherical autoencoder with diffusion generative model | Low-resolution and high-resolution climate model output | Structure preservation vs convolutional baselines | Preserves physical structures better and enables zero-shot super-resolution |

## Gaps and open questions

- The abstracts do not report quantitative metrics or benchmark datasets for several models, making direct comparison difficult [P1, P5]. [1], [5]
- Limitations are not stated in any of the abstracts, leaving uncertainty about failure modes and computational costs [P1, P2, P3, P4, P5]. [1], [2], [3], [4], [5]

## References

1. Michael Aich, Andreas Fürst, Florian Sestak, et al.. WIND: Weather Inverse Diffusion for Zero-Shot Atmospheric Modeling. arxiv, 2026. https://arxiv.org/abs/2602.03924v2
2. Lianjun Wu, Shengchen Zhu, Yuxuan Liu, et al.. PuYun-LDM: A Latent Diffusion Model for High-Resolution Ensemble Weather Forecasts. arxiv, 2026. https://arxiv.org/abs/2602.11807v2
3. Yilin Zhuang, Karthik Duraisamy. LaDCast: A Latent Diffusion Model for Medium-Range Ensemble Weather Forecasting. arxiv, 2025. https://arxiv.org/abs/2506.09193v1
4. Jean Kossaifi, Nikola Kovachki, Morteza Mardani, et al.. Demystifying Data-Driven Probabilistic Medium-Range Weather Forecasting. arxiv, 2026. https://arxiv.org/abs/2601.18111v1
5. Johannes Meuer, Maximilian Witte, Étiénne Plésiat, et al.. Field-Space Autoencoder for Scalable Climate Emulators. arxiv, 2026. https://arxiv.org/abs/2601.15102v1

## Appendix: generation parameters

- Run id: 20260917T140136Z-diffusion-models-for-weather-forecasting
- Model: deepseek-v4-pro
- Language: en
- Prompt analyzer_v2: analyzer_v2
- Prompt planner_v1: planner_v1
- Prompt screener_v1: screener_v1
- Prompt synthesizer_v2: synthesizer_v2
- Prompt verifier_v1: verifier_v1
