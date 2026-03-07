const path = require('path');
//const ComponentTaggerPlugin = require('@alipay/yfd-air-component-tagger-plugin');
const HtmlWebpackPlugin = require('html-webpack-plugin');
const webpack = require('webpack');

module.exports = {
    context: __dirname,
    entry: './src/main.tsx',
    output: {
        path: path.resolve(__dirname, 'dist'),
        filename: 'bundle.js',
        publicPath: '/'
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
            inject: 'body',
            favicon: path.resolve(__dirname, 'smart-eats.png'),
            hash: true
        }),
        new webpack.DefinePlugin({
            'process.env.APP_API_BASE_URL': JSON.stringify(process.env.APP_API_BASE_URL || ''),
            'process.env.APP_SHOW_ONECLICK_LOGIN': JSON.stringify(process.env.APP_SHOW_ONECLICK_LOGIN || 'false')
        }),
       // new ComponentTaggerPlugin(process.env)
    ]
};
