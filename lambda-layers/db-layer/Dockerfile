FROM public.ecr.aws/lambda/python:3.12 AS build-stage

# 作業ディレクトリを設定
WORKDIR /layer

# requirements.txtをコピー
COPY requirements.txt .

# パッケージをインストール
RUN pip install -r requirements.txt -t python/ --no-cache-dir

# パッケージをコピー
FROM scratch
COPY --from=build-stage /layer/python /
