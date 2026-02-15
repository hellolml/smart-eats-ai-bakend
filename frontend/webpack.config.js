const path = require('path');
//const ComponentTaggerPlugin = require('@alipay/yfd-air-component-tagger-plugin');
const HtmlWebpackPlugin = require('html-webpack-plugin');
const webpack = require('webpack');

module.exports = {
    entry: './src/main.tsx',
    output: {
        path: path.resolve(__dirname, 'dist'),
        filename: 'bundle.js'
    },
    devServer:{
        static: {
            directory: path.join(__dirname, 'public'),
        },
        port: 3001,
        proxy: [
            {
                context: ['/api'],
                target: process.env.BACKEND_ORIGIN || 'http://127.0.0.1:8000',
                changeOrigin: true
            }
        ],
        allowedHosts: 'all',
        hot: true
    },
    module: {
        rules: [
            {
                test: /\.(js|jsx|ts|tsx)$/,
                exclude: /node_modules/,
                use: {
                    loader: 'babel-loader',
                    options: {
                        presets: ['@babel/preset-env', '@babel/preset-react', '@babel/preset-typescript'],
                    },
                }
                
            },
            {
                test: /\.css$/,
                use: ['style-loader', 'css-loader', '', 'postcss-loader']
            }
        ]
    },
    resolve: {
        extensions: ['.js', '.jsx', '.ts', '.tsx'],
        alias: {
            '@': path.resolve(__dirname, 'src'),
        }
    },
    plugins: [
        new HtmlWebpackPlugin({
            template: './index.html',
            inject: 'body'
        }),
        new webpack.DefinePlugin({
            'process.env.APP_API_BASE_URL': JSON.stringify(process.env.APP_API_BASE_URL || '')
        }),
       // new ComponentTaggerPlugin(process.env)
    ]
};
